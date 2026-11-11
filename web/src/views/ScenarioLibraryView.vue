<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import type { ScenarioSummary } from '@/api/types'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { vReveal } from '@/directives/reveal'
import { useGameStore } from '@/stores/game'
import { useScenariosStore } from '@/stores/scenarios'
import type { PlayerErrorInfo } from '@/utils/errors'
import { formatDateTime } from '@/utils/labels'
import { describeScenarioError, formatScenarioStats } from '@/utils/scenarios'

const router = useRouter()
const store = useScenariosStore()
const game = useGameStore()

const searchText = ref(store.query)
/** 列表内「删除」二次确认：同一时刻只武装一行（确认/取消前不发请求）。 */
const deleteArmedId = ref('')
const createError = ref<PlayerErrorInfo | null>(null)

const showRetry = computed<boolean>(() => store.error !== null)

async function load(): Promise<void> {
  try {
    await store.loadList()
  } catch {
    // 错误已入 store.error，由 ErrorBanner 展示（手动重试）
  }
}

async function onSearch(): Promise<void> {
  try {
    await store.search(searchText.value)
  } catch {
    // 同上
  }
}

async function onPrev(): Promise<void> {
  try {
    await store.prevPage()
  } catch {
    // 同上
  }
}

async function onNext(): Promise<void> {
  try {
    await store.nextPage()
  } catch {
    // 同上
  }
}

/** 按此场景开局：body `{scenario_id}`（与 card_id/scenario 互斥），带幂等键。 */
async function onStart(item: ScenarioSummary): Promise<void> {
  createError.value = null
  deleteArmedId.value = ''
  try {
    const gameId = await game.createGame({ scenario_id: item.scenario_id })
    await router.push({
      name: 'game',
      params: { id: gameId },
      query: { scenario_id: item.scenario_id },
    })
  } catch (error) {
    createError.value = describeScenarioError(error)
  }
}

/** 删除：第一次点击只武装按钮，第二次才发 DELETE（软删；不影响已开局存档）。 */
async function onDelete(item: ScenarioSummary): Promise<void> {
  if (deleteArmedId.value !== item.scenario_id) {
    deleteArmedId.value = item.scenario_id
    return
  }
  const removed = await store.remove(item.scenario_id)
  if (!removed) {
    return
  }
  deleteArmedId.value = ''
  // 删完当前页可能空了：回退到上一页（没有上一页就保持空态提示）
  if (store.items.length === 0 && store.offset > 0) {
    try {
      await store.loadList({ offset: Math.max(0, store.offset - store.limit) })
    } catch {
      // 忽略：错误已入 store.error
    }
  }
}

function onCancelDelete(): void {
  deleteArmedId.value = ''
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="scenario-library-view">
    <header class="game-head">
      <h2>场景库</h2>
      <p class="game-meta">
        共 {{ store.total }} 个场景 · 第 {{ store.page }} / {{ store.totalPages }} 页
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small" :to="{ name: 'scenario-import' }">导入场景</RouterLink>
      <span class="muted">
        上传一份自己写的场景/剧情 JSON，导入后就能按它开新局（内置 4 个场景不受影响）。
      </span>
    </div>

    <form v-reveal class="panel cards-tools" @submit.prevent="onSearch">
      <label class="field">
        <span>搜索标题 / 作者 / slug</span>
        <input
          v-model="searchText"
          class="input"
          type="text"
          maxlength="80"
          placeholder="例如：余烬哨站"
          :disabled="store.loading"
        />
      </label>
      <button class="btn" type="submit" :disabled="store.loading">搜索</button>
      <button class="btn btn-secondary" type="button" :disabled="store.loading" @click="load">
        刷新
      </button>
    </form>

    <ErrorBanner
      :title="store.error?.title"
      :message="store.error?.message ?? ''"
      :detail="store.error?.detail"
      :retryable="showRetry"
      @retry="load"
    />
    <ErrorBanner
      :title="createError?.title"
      :message="createError?.message ?? ''"
      :detail="createError?.detail"
    />

    <p v-if="store.loading" class="muted" role="status">正在读取场景库…</p>
    <p v-else-if="store.items.length === 0 && store.error === null" class="muted" role="status">
      还没有导入的场景。可以先
      <RouterLink :to="{ name: 'scenario-import' }">导入一份场景 JSON</RouterLink>
      ，或直接回
      <RouterLink :to="{ name: 'home' }">首页</RouterLink>
      玩内置场景。
    </p>

    <ul v-else class="plain-list scenario-list">
      <li v-for="item in store.items" :key="item.scenario_id" v-reveal class="panel scenario-item">
        <div class="scenario-head">
          <RouterLink
            class="scenario-title"
            :to="{ name: 'scenario-detail', params: { scenarioId: item.scenario_id } }"
          >
            {{ item.title }}
          </RouterLink>
          <span class="world-tag">场景 v{{ item.schema_version }}</span>
          <span v-if="item.author !== ''" class="muted scenario-author">作者：{{ item.author }}</span>
        </div>
        <p class="scenario-meta muted">
          {{ formatScenarioStats(item.summary) }} · 导入于 {{ formatDateTime(item.created_at) }}
        </p>
        <p v-if="item.description !== ''" class="scenario-desc">{{ item.description }}</p>
        <p v-if="item.warnings.length > 0" class="muted scenario-warnings-count">
          导入提示 {{ item.warnings.length }} 条（不阻断，可继续游玩）
        </p>
        <div class="game-tools scenario-actions">
          <button
            class="btn btn-small"
            type="button"
            :disabled="game.busy || store.deleteBusy"
            @click="onStart(item)"
          >
            {{ game.busy ? '正在开局…' : '按此场景开局' }}
          </button>
          <RouterLink
            class="btn btn-small btn-secondary"
            :to="{ name: 'scenario-detail', params: { scenarioId: item.scenario_id } }"
          >
            查看详情
          </RouterLink>
          <button
            v-if="deleteArmedId !== item.scenario_id"
            class="btn btn-small btn-secondary"
            type="button"
            :disabled="store.deleteBusy"
            @click="onDelete(item)"
          >
            删除
          </button>
          <template v-else>
            <button
              class="btn btn-small"
              type="button"
              :disabled="store.deleteBusy"
              @click="onDelete(item)"
            >
              {{ store.deleteBusy ? '删除中…' : '确认删除（不可恢复）' }}
            </button>
            <button class="btn btn-small btn-secondary" type="button" @click="onCancelDelete">
              取消
            </button>
          </template>
        </div>
      </li>
    </ul>

    <ErrorBanner
      :title="store.deleteError?.title"
      :message="store.deleteError?.message ?? ''"
      :detail="store.deleteError?.detail"
    />

    <div class="game-tools">
      <button class="btn btn-small btn-secondary" type="button" :disabled="!store.canPrev" @click="onPrev">
        ← 上一页
      </button>
      <button class="btn btn-small btn-secondary" type="button" :disabled="!store.canNext" @click="onNext">
        下一页 →
      </button>
      <span class="muted">手动翻页/搜索。</span>
    </div>
  </section>
</template>
