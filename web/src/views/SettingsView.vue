<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { vReveal } from '@/directives/reveal'
import { useFreeCraftStore } from '@/stores/freeCraft'
import { useLlmSettingsStore } from '@/stores/llmSettings'
import {
  checkApiKeyInput,
  describeLlmTest,
  formatMaskedKey,
  labelLlmEnabled,
  labelLlmSource,
  LLM_DEFAULT_API_BASE,
  LLM_DEFAULT_MODEL,
  LLM_DEFAULT_PROVIDER,
} from '@/utils/llm'

const store = useLlmSettingsStore()
const freeCraft = useFreeCraftStore()

/** 表单字段（明文 key 只存在组件内存；保存成功后立即清空）。 */
const apiKey = ref('')
const provider = ref(LLM_DEFAULT_PROVIDER)
const model = ref(LLM_DEFAULT_MODEL)
const apiBase = ref(LLM_DEFAULT_API_BASE)
const localError = ref('')

/**
 * 焦点管理（041 a11y 批次）：二次确认面板（自由制造风险 / 清除本机 key）打开即聚焦到确认按钮，
 * `Esc` 取消，取消后焦点归还触发开关/按钮；两个确认面板互不抢焦点。
 */
const freeCraftSwitchEl = ref<HTMLButtonElement | null>(null)
const freeCraftConfirmEl = ref<HTMLButtonElement | null>(null)
const clearButtonEl = ref<HTMLButtonElement | null>(null)
const clearConfirmEl = ref<HTMLButtonElement | null>(null)

watch(
  () => freeCraft.confirmArmed,
  (armed, wasArmed) => {
    void nextTick(() => {
      if (armed) {
        freeCraftConfirmEl.value?.focus()
      } else if (wasArmed) {
        freeCraftSwitchEl.value?.focus()
      }
    })
  },
)

watch(
  () => store.clearArmed,
  (armed, wasArmed) => {
    void nextTick(() => {
      if (armed) {
        clearConfirmEl.value?.focus()
      } else if (wasArmed) {
        clearButtonEl.value?.focus()
      }
    })
  },
)

function onFreeCraftEscape(): void {
  if (freeCraft.confirmArmed) {
    freeCraft.disarmEnable()
  }
}

function onClearEscape(): void {
  if (store.clearArmed) {
    store.disarmClear()
  }
}

const status = computed(() => store.status)
const showRetry = computed<boolean>(() => store.loadError !== null)
const freeCraftStatus = computed(() => freeCraft.status)
const freeCraftToggleDisabled = computed<boolean>(
  () =>
    freeCraftStatus.value === null ||
    freeCraft.loading ||
    freeCraft.saveBusy ||
    (!freeCraft.enabled && !freeCraft.canEnable),
)
const sourceLabel = computed<string>(() =>
  status.value === null ? '读取中…' : labelLlmSource(status.value.source),
)
const testResultText = computed<string>(() =>
  store.lastTest === null ? '' : describeLlmTest(store.lastTest),
)

async function load(): Promise<void> {
  try {
    await store.load()
  } catch {
    // 错误已入 store.loadError，由 ErrorBanner 展示（手动重试）
  }
}

async function loadFreeCraft(): Promise<void> {
  try {
    await freeCraft.load()
  } catch {
    // 错误已入 freeCraft.loadError，由 ErrorBanner 展示（手动重试）
  }
}

/** 开关点击：已开启则直接关闭；未开启先弹风险确认，不直接请求。 */
async function onFreeCraftToggle(): Promise<void> {
  if (freeCraft.saveBusy) return
  if (freeCraft.enabled) {
    await freeCraft.disable()
    return
  }
  if (freeCraft.canEnable) {
    freeCraft.armEnable()
  }
}

async function onConfirmFreeCraft(): Promise<void> {
  await freeCraft.enable()
}

async function onSave(): Promise<void> {
  localError.value = ''
  const check = checkApiKeyInput(apiKey.value)
  if (!check.ok) {
    localError.value = check.error
    return
  }
  const saved = await store.save({
    api_key: apiKey.value.trim(),
    provider: provider.value,
    model: model.value.trim() === '' ? LLM_DEFAULT_MODEL : model.value.trim(),
    api_base: apiBase.value.trim() === '' ? LLM_DEFAULT_API_BASE : apiBase.value.trim(),
  })
  if (saved) {
    // 安全：明文 key 不留在输入框/内存
    apiKey.value = ''
    // LLM 开关变化会影响自由制造的 available 状态。
    void loadFreeCraft()
  }
}

async function onTest(): Promise<void> {
  localError.value = ''
  await store.test()
}

/** 清除本机 key：第一次点击只武装确认按钮，第二次才发 DELETE。 */
async function onClear(): Promise<void> {
  localError.value = ''
  if (!store.clearArmed) {
    store.armClear()
    return
  }
  const cleared = await store.clear()
  if (cleared) {
    apiKey.value = ''
    void loadFreeCraft()
  }
}

onMounted(() => {
  void load()
  void loadFreeCraft()
})
</script>

<template>
  <section class="settings-view">
    <header class="game-head">
      <h2>设置</h2>
      <p class="game-meta">
        配置你自己的 DeepSeek API Key：保存后当前进程立即生效（无需重启），下次启动自动恢复。自由制造开关也在此页，开启前会提示风险。
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'home' }">← 返回首页</RouterLink>
      <span class="muted">没配也能玩：默认离线模板叙事；配置后由大模型叙事。</span>
    </div>

    <ErrorBanner
      :title="store.loadError?.title"
      :message="store.loadError?.message ?? ''"
      :detail="store.loadError?.detail"
      :retryable="showRetry"
      @retry="load"
    />
    <p v-if="store.loading" class="muted" role="status">正在读取当前 AI 配置…</p>

    <!-- 状态卡：来源（env/user/none）、模型、打码 key、是否已启用、detail 原文 -->
    <section v-if="status !== null" v-reveal class="panel llm-status-card" aria-label="当前 AI 状态">
      <div class="llm-status-head">
        <h3 class="panel-title">当前 AI 状态</h3>
        <span class="world-tag llm-source-tag">{{ sourceLabel }}</span>
        <span class="world-tag" :class="{ 'world-tag-constant': !status.enabled }">
          {{ labelLlmEnabled(status.enabled) }}
        </span>
      </div>
      <dl class="status-grid">
        <dt>配置来源</dt>
        <dd>
          {{ sourceLabel }}
          <span class="muted">{{ status.configured ? '（已配置可用）' : '（未配置，离线模板叙事）' }}</span>
        </dd>
        <dt>供应商 / 模型</dt>
        <dd>{{ status.provider }} / {{ status.model }}</dd>
        <dt>API 地址</dt>
        <dd><code>{{ status.api_base }}</code></dd>
        <dt>Key（打码）</dt>
        <dd>{{ formatMaskedKey(status.masked_key) }}</dd>
        <dt>本机保存 key</dt>
        <dd>{{ status.has_saved_user_key ? '是' : '否' }}</dd>
      </dl>
      <p class="llm-status-detail">{{ status.detail }}</p>
    </section>

    <!-- 自由制造开关：默认关闭；开启前必须确认 Resolver 内调 LLM 的风险 -->
    <section v-reveal class="panel free-craft-card" aria-label="自由制造">
      <div class="llm-status-head">
        <h3 class="panel-title">自由制造</h3>
        <span class="world-tag" :class="{ 'world-tag-constant': !freeCraft.enabled }">
          {{ freeCraft.enabled ? '已开启' : '已关闭' }}
        </span>
        <span v-if="freeCraftStatus !== null" class="world-tag free-craft-source">
          {{ freeCraftStatus.source === 'user' ? '本机设置' : freeCraftStatus.source === 'env' ? '环境默认' : '默认关闭' }}
        </span>
      </div>
      <p class="field-hint muted">
        不命中固定配方时，允许大模型临时生成制造草稿。开启后会在裁决链路内调用大模型，可能因重试产生额外费用；建议只在单人本地使用。
      </p>

      <ErrorBanner
        :title="freeCraft.loadError?.title"
        :message="freeCraft.loadError?.message ?? ''"
        :detail="freeCraft.loadError?.detail"
        :retryable="freeCraft.loadError !== null"
        @retry="loadFreeCraft"
      />
      <p v-if="freeCraft.loading" class="muted" role="status">正在读取自由制造状态…</p>

      <div v-if="freeCraftStatus !== null" class="free-craft-row">
        <div class="free-craft-copy">
          <p class="free-craft-detail">{{ freeCraftStatus.detail }}</p>
          <p v-if="!freeCraftStatus.available && !freeCraftStatus.enabled" class="field-hint muted">
            先在下方配置并启用大模型 API Key，才能开启自由制造。
          </p>
        </div>
        <button
          ref="freeCraftSwitchEl"
          type="button"
          class="free-craft-switch"
          role="switch"
          :aria-checked="freeCraft.enabled"
          :aria-label="freeCraft.enabled ? '关闭自由制造' : '开启自由制造'"
          :disabled="freeCraftToggleDisabled"
          @click="onFreeCraftToggle"
        >
          <span class="free-craft-thumb" aria-hidden="true" />
        </button>
      </div>

      <div
        v-if="freeCraft.confirmArmed"
        class="panel free-craft-warning"
        role="alertdialog"
        aria-label="自由制造风险确认"
        aria-describedby="free-craft-risk-text"
        @keydown.esc="onFreeCraftEscape"
      >
        <p id="free-craft-risk-text">
          <strong>开启前确认：</strong>
          自由制造会在裁决/重试链路内调用大模型，可能重复计费；它不属于纯确定性裁决。
        </p>
        <div class="game-tools">
          <button
            ref="freeCraftConfirmEl"
            class="btn"
            type="button"
            :disabled="freeCraft.saveBusy"
            @click="onConfirmFreeCraft"
          >
            {{ freeCraft.saveBusy ? '开启中…' : '确认开启' }}
          </button>
          <button
            class="btn btn-small btn-secondary"
            type="button"
            :disabled="freeCraft.saveBusy"
            @click="freeCraft.disarmEnable()"
          >
            取消
          </button>
        </div>
      </div>

      <p v-if="freeCraft.saveNotice !== ''" class="panel llm-notice" role="status">
        {{ freeCraft.saveNotice }}
      </p>
      <ErrorBanner
        :title="freeCraft.saveError?.title"
        :message="freeCraft.saveError?.message ?? ''"
        :detail="freeCraft.saveError?.detail"
      />
    </section>

    <!-- 表单：API Key / 供应商 / 模型 / 高级 API 地址；保存并启用、测试、清除 -->
    <form v-reveal class="panel llm-form" @submit.prevent="onSave">
      <h3 class="panel-title">填写你自己的 API Key</h3>
      <ErrorBanner :message="localError" />

      <label class="field">
        <span>DeepSeek API Key</span>
        <input
          v-model="apiKey"
          class="input"
          type="password"
          autocomplete="off"
          spellcheck="false"
          maxlength="512"
          placeholder="sk-..."
          :disabled="store.saveBusy || store.clearBusy"
        />
        <span class="field-hint muted">
          出于安全，本页不回显已保存的 key；保存时必须重新输入完整 key。key 只保存在本机。
        </span>
      </label>

      <div class="llm-form-row">
        <label class="field">
          <span>供应商</span>
          <select v-model="provider" class="input" :disabled="store.saveBusy || store.clearBusy">
            <option value="deepseek">deepseek</option>
          </select>
        </label>
        <label class="field">
          <span>模型</span>
          <input
            v-model="model"
            class="input"
            type="text"
            maxlength="128"
            placeholder="deepseek-flash"
            :disabled="store.saveBusy || store.clearBusy"
          />
          <span class="field-hint muted">默认 deepseek-flash；也可填 deepseek-chat。</span>
        </label>
      </div>

      <details class="advanced">
        <summary>高级：API 地址</summary>
        <label class="field">
          <span>API 地址</span>
          <input
            v-model="apiBase"
            class="input"
            type="text"
            maxlength="512"
            placeholder="https://api.deepseek.com"
            :disabled="store.saveBusy || store.clearBusy"
          />
          <span class="field-hint muted">一般不用改；默认 https://api.deepseek.com。</span>
        </label>
      </details>

      <div class="game-tools llm-actions" @keydown.esc="onClearEscape">
        <button class="btn" type="submit" :disabled="store.saveBusy || store.clearBusy">
          {{ store.saveBusy ? '保存中…' : '保存并启用' }}
        </button>
        <button
          class="btn btn-secondary"
          type="button"
          :disabled="store.testBusy || store.saveBusy || store.clearBusy"
          @click="onTest"
        >
          {{ store.testBusy ? '测试中…' : '测试连接' }}
        </button>
        <button
          v-if="!store.clearArmed"
          ref="clearButtonEl"
          class="btn btn-small btn-secondary"
          type="button"
          :disabled="!store.canClear || store.clearBusy || store.saveBusy"
          @click="onClear"
        >
          清除本机 key
        </button>
        <template v-else>
          <button
            ref="clearConfirmEl"
            class="btn btn-small"
            type="button"
            :disabled="store.clearBusy"
            @click="onClear"
          >
            {{ store.clearBusy ? '清除中…' : '确认清除（不可恢复）' }}
          </button>
          <button class="btn btn-small btn-secondary" type="button" @click="store.disarmClear()">
            取消
          </button>
        </template>
      </div>
      <p v-if="!store.canClear" class="field-hint muted">
        本机还没有保存过 key，所以「清除」不可用（开发环境 .env 的 key 由环境变量提供，不在这里清除）。
      </p>
      <p class="muted llm-test-hint">
        「测试连接」测的是「当前已生效」的配置：改了 key 请先「保存并启用」，再测试。
      </p>
    </form>

    <!-- 保存/清除结果的轻提示 -->
    <p v-if="store.saveNotice !== ''" class="panel llm-notice" role="status">
      {{ store.saveNotice }}
    </p>
    <p v-if="store.clearNotice !== ''" class="panel llm-notice" role="status">
      {{ store.clearNotice }}
    </p>
    <ErrorBanner
      :title="store.saveError?.title"
      :message="store.saveError?.message ?? ''"
      :detail="store.saveError?.detail"
    />
    <ErrorBanner
      :title="store.clearError?.title"
      :message="store.clearError?.message ?? ''"
      :detail="store.clearError?.detail"
    />

    <!-- 测试连接结果 -->
    <section v-if="store.lastTest !== null" v-reveal class="panel llm-test-panel" aria-live="polite">
      <h3 class="panel-title">连接测试结果</h3>
      <p :class="store.lastTest.ok ? 'llm-test-ok' : 'llm-test-fail'">{{ testResultText }}</p>
      <dl class="status-grid">
        <dt>供应商 / 模型</dt>
        <dd>{{ store.lastTest.provider }} / {{ store.lastTest.model }}</dd>
        <dt>往返耗时</dt>
        <dd>{{ store.lastTest.latency_ms === null ? '—' : `${store.lastTest.latency_ms} 毫秒` }}</dd>
      </dl>
      <p class="muted">测试只发一次最小请求，不改游戏状态、不改已保存配置。</p>
    </section>

    <!-- 安全提示 -->
    <section v-reveal class="panel llm-security" aria-label="安全提示">
      <h3 class="panel-title">安全提示</h3>
      <ul class="plain-list">
        <li>
          Key 只保存在本机 <code>data/llm.credentials.json</code>（不入库、不进 git、页面不回显明文）。
        </li>
        <li>不要使用共享账号的 key；如果怀疑泄露，请到 DeepSeek 控制台吊销后重新填写。</li>
        <li>
          优先级：<code>.env</code> 里的开发 key &gt; 本机保存的 key；显式
          <code>TEXTGAME_LLM_ENABLED=false</code> 时始终离线。
        </li>
        <li>页面不会把 key 写进 localStorage/sessionStorage；保存成功后密码框立即清空。</li>
        <li>没配置也完全能玩：离线模板叙事；配置后当前进程立即切换为大模型叙事。</li>
      </ul>
    </section>
  </section>
</template>
