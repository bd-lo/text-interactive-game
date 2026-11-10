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
const selectedTurn = computed<number>(() => store.selectedEvent?.turn_id ?? 0)
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
      <h2>回放</h2>
      <p class="game-meta">
        逐条重看这一局的经历 · 共 {{ store.loadedCount }} 条记录
        <template v-if="store.hasMore"> · 还有更早的记录</template>
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'game', params: { id: gameId } }">
        返回对局
      </RouterLink>
      <button class="btn btn-small" type="button" :disabled="busy || !store.hasMore" @click="onLoadMore">
        {{ store.hasMore ? '看更早的记录' : '已经到最早了' }}
      </button>
    </div>

    <ErrorBanner :message="store.error" :retryable="showRetry" @retry="load" />

    <ReplayTimeline
      v-if="store.loadedCount > 0"
      :count="store.loadedCount"
      :selected-index="store.selectedIndex"
      :selected-seq="store.selectedSeq"
      :last-seq="store.lastSeq"
      :selected-turn="selectedTurn"
      :busy="busy"
      @select="onSelect"
    />
    <ReplayEventPanel :event="store.selectedEvent" />

    <p v-if="!busy && store.loadedCount === 0 && store.error === ''" class="muted">
      该存档暂无可回放事件（或事件尚未生成）。
    </p>
    <p class="muted replay-note">
      回放只是重看，不会改变对局。用下方的进度条或按钮前后翻看每一次行动。
    </p>
    <p class="muted replay-note">查看/背包等只读指令不会写入冒险记录，也不会出现在回放里。</p>
  </section>
</template>
