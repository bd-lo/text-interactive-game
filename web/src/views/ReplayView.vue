<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import ReplayEventPanel from '@/components/ReplayEventPanel.vue'
import ReplayTimeline from '@/components/ReplayTimeline.vue'
import { useReplayStore } from '@/stores/replay'

const route = useRoute()
const store = useReplayStore()

const gameId = computed<string>(() => String(route.params.id ?? ''))
const showRetry = computed<boolean>(() => store.error !== '')
const busy = computed<boolean>(() => store.loading)

async function load(): Promise<void> {
  const id = gameId.value
  if (id === '') {
    return
  }
  try {
    await store.loadFirstPage(id)
  } catch {
    // 只读请求失败：错误已入 store.error，由 ErrorBanner 提供手动「重试加载」
  }
}

async function onLoadMore(): Promise<void> {
  try {
    await store.loadMore()
  } catch {
    // 同上
  }
}

function onSelect(index: number): void {
  store.select(index)
}

onMounted(() => {
  void load()
})

watch(gameId, () => {
  void load()
})
</script>

<template>
  <section class="replay-view">
    <header class="game-head">
      <h2>回放 <code>{{ gameId }}</code></h2>
      <p class="game-meta">
        只读时间轴 · 第 {{ store.loadedCount }} 条已加载 · 已加载末 seq {{ store.lastSeq }} ·
        末窗口 to_seq {{ store.toSeq }}
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'game', params: { id: gameId } }">
        返回对局
      </RouterLink>
      <button class="btn btn-small" type="button" :disabled="busy || !store.hasMore" @click="onLoadMore">
        {{ store.hasMore ? '加载更多（下一页）' : '已到末尾' }}
      </button>
      <span class="muted">
        分页口径：from_seq 从 1 起、limit 默认 200（上限 500），事件按全局 seq 升序；只读、零写入、无自动重试。
      </span>
    </div>

    <ErrorBanner :message="store.error" :retryable="showRetry" @retry="load" />

    <ReplayTimeline
      v-if="store.loadedCount > 0"
      :count="store.loadedCount"
      :selected-index="store.selectedIndex"
      :selected-seq="store.selectedSeq"
      :last-seq="store.lastSeq"
      :busy="busy"
      @select="onSelect"
    />
    <ReplayEventPanel :event="store.selectedEvent" />

    <p v-if="!busy && store.loadedCount === 0 && store.error === ''" class="muted">
      该存档暂无可回放事件（或事件尚未生成）。
    </p>
    <p class="muted replay-note">
      数据源：GET /api/v1/games/{id}/replay（`result.narrative` 文本回放）；不展示原始 params，
      不写对局状态，不推演权威版本（第 23 章 §23.3 / D-M6-3）。
    </p>
  </section>
</template>
