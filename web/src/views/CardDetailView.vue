<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import CardFields from '@/components/CardFields.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { useCardsStore } from '@/stores/cards'
import { useGameStore } from '@/stores/game'

const route = useRoute()
const router = useRouter()
const cards = useCardsStore()
const game = useGameStore()

const cardId = computed<string>(() => String(route.params.cardId ?? ''))
const card = computed(() => cards.detail)
const errorMessage = computed<string>(() => (cards.detailError !== '' ? cards.detailError : game.error))
const showRetry = computed<boolean>(() => cards.detailError !== '')
const startBusy = computed<boolean>(() => game.busy)

async function load(): Promise<void> {
  if (cardId.value === '') {
    return
  }
  try {
    await cards.loadDetail(cardId.value)
  } catch {
    // 404/422 等已转成中文错误，由 ErrorBanner 展示
  }
}

async function onStartGame(): Promise<void> {
  try {
    // D-M6-2 加法语义：POST /api/v1/games {card_id}（幂等键由 gameStore 生成/复用）
    const gameId = await game.createGame({ card_id: cardId.value })
    await router.push({ name: 'game', params: { id: gameId } })
  } catch {
    // 错误已入 game.error（404/422 等），由 ErrorBanner 展示
  }
}

onMounted(() => {
  void load()
})

watch(cardId, () => {
  game.error = ''
  void load()
})
</script>

<template>
  <section class="card-detail-view">
    <header class="game-head">
      <h2>人物卡详情 <code>{{ cardId }}</code></h2>
      <p class="game-meta">用这张人物卡开始一局新的冒险</p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'cards' }">← 返回卡库</RouterLink>
      <button class="btn" type="button" :disabled="startBusy || card === null" @click="onStartGame">
        {{ startBusy ? '创建中…' : '开始游戏' }}
      </button>
    </div>

    <ErrorBanner :message="errorMessage" :retryable="showRetry" @retry="load" />

    <p v-if="cards.detailLoading" class="muted" role="status">正在加载人物卡…</p>
    <p v-else-if="card === null && cards.detailError === ''" class="muted" role="status">
      未找到人物卡（ID：{{ cardId }}）。
    </p>
    <CardFields v-else-if="card !== null" :card="card" />

    <p v-if="card !== null" class="muted">
      人物卡由服务端校验后保存；这里只做只读展示。
    </p>
  </section>
</template>
