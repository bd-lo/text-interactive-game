<script setup lang="ts">
/**
 * 生成草稿预览（M6-028）：9 字段只读展示 + 「LLM 草稿」/「模板草稿」徽章 +
 * warnings/errors 中文列表 + 可编辑 JSON 编辑器（重新校验通过前禁用保存）。
 *
 * 安全（第 23 章 §23.7）：全部文本走 `{{ }}` 插值，无 HTML 注入；JSON 只用 `<textarea>` 承载。
 */
import { computed } from 'vue'

import CardFields from '@/components/CardFields.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { useCardGenerateStore } from '@/stores/cardGenerate'
import {
  describeGenerateWarning,
  formatCardValidationError,
} from '@/utils/cardGenerate'

const store = useCardGenerateStore()

const warnings = computed(() => store.warnings)
const draftErrors = computed(() => store.draftErrors)
const editedErrors = computed(() => store.editedErrors)

function onJsonInput(): void {
  store.markEdited()
}

const saveHint = computed<string>(() => {
  if (store.savedCardId !== '') {
    return '已保存到卡库。'
  }
  return store.canSave ? '校验通过，可以保存。' : 'JSON 未通过校验，保存已禁用。'
})

async function onRevalidate(): Promise<void> {
  await store.revalidate()
}

async function onSave(): Promise<void> {
  await store.save()
}
</script>

<template>
  <section
    v-if="store.draft !== null"
    class="panel draft-preview"
    data-testid="draft-preview"
    aria-label="生成草稿预览"
  >
    <div class="draft-head">
      <h3 class="panel-title">草稿预览</h3>
      <span
        class="draft-badge"
        :class="store.templateFallback ? 'draft-badge-template' : 'draft-badge-llm'"
        data-testid="draft-badge"
      >
        {{ store.badgeLabel }}
      </span>
    </div>
    <p class="muted">
      草稿尚未保存、不会自动开局；确认（可先编辑并重新校验）后再点「保存到卡库」。
    </p>

    <CardFields :card="store.draft" />

    <div v-if="warnings.length > 0" class="panel draft-notice">
      <h4 class="panel-subtitle">生成提示</h4>
      <ul class="plain-list">
        <li v-for="(warning, index) in warnings" :key="warning.code + String(index)">
          {{ describeGenerateWarning(warning) }}
        </li>
      </ul>
    </div>

    <div v-if="draftErrors.length > 0" class="panel draft-notice">
      <h4 class="panel-subtitle">草稿校验未通过</h4>
      <ul class="plain-list">
        <li v-for="(error, index) in draftErrors" :key="'draft-' + String(index)">
          {{ formatCardValidationError(error) }}
        </li>
      </ul>
    </div>

    <details class="advanced draft-editor" open>
      <summary>编辑草稿 JSON（可选；改完必须重新校验才能保存）</summary>
      <textarea
        v-model="store.editedText"
        data-testid="draft-json"
        class="input card-json-input"
        rows="14"
        aria-label="草稿 JSON（9 字段）"
        :disabled="store.editedBusy"
        @input="onJsonInput"
      ></textarea>
      <div class="game-tools">
        <button
          class="btn"
          type="button"
          data-testid="revalidate-button"
          :disabled="store.editedBusy"
          @click="onRevalidate"
        >
          {{ store.editedBusy ? '校验中…' : '重新校验' }}
        </button>
        <button
          class="btn"
          type="button"
          data-testid="save-card-button"
          :disabled="!store.canSave"
          @click="onSave"
        >
          {{ store.saveBusy ? '保存中…' : '保存到卡库' }}
        </button>
        <span class="muted" role="status">{{ saveHint }}</span>
      </div>
    </details>

    <ErrorBanner :message="store.editedError" />
    <div v-if="editedErrors.length > 0" class="panel draft-notice">
      <h4 class="panel-subtitle">重新校验未通过（服务端 validate）</h4>
      <ul class="plain-list">
        <li v-for="(error, index) in editedErrors" :key="'edited-' + String(index)">
          {{ formatCardValidationError(error) }}
        </li>
      </ul>
    </div>

    <ErrorBanner
      :title="store.saveErrorTitle"
      :message="store.saveError"
      :detail="store.saveErrorDetail"
    />
  </section>
</template>
