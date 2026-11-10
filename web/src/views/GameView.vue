<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'

import CommandInput from '@/components/CommandInput.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import StatusPanel from '@/components/StatusPanel.vue'
import TurnTextStream from '@/components/TurnTextStream.vue'
import { useGameStore } from '@/stores/game'

const route = useRoute()
const store = useGameStore()

const gameId = computed<string>(() => String(route.params.id ?? ''))
const busy = computed<boolean>(() => store.busy)
const canSubmit = computed<boolean>(() => store.canSubmit)
const showRetry = computed<boolean>(() => store.canRetry)

async function ensureLoaded(): Promise<void> {
  const id = gameId.value
  if (id === '') {
    return
  }
  if (store.gameId === id && store.version > 0) {
    return
  }
  try {
    await store.loadGame(id)
  } catch {
    // 错误已写入 store.error（如 404）
  }
}

async function onRefresh(): Promise<void> {
  try {
    await store.refreshAll()
  } catch {
    // 保留 store.error
  }
}

async function onSubmit(text: string): Promise<void> {
  await store.submitTurn(text)
}

async function onRetry(): Promise<void> {
  await store.retry()
}

onMounted(() => {
  void ensureLoaded()
})

watch(gameId, () => {
  void ensureLoaded()
})
</script>

<template>
  <section class="game-view">
    <header class="game-head">
      <h2>对局 <code>{{ store.gameId === '' ? gameId : store.gameId }}</code></h2>
      <p class="game-meta">
        turn {{ store.turn }} · version {{ store.version }} · snapshot {{ store.snapshotVersion }} ·
        {{ store.status }} · HP {{ store.hpLabel }}
      </p>
    </header>

    <ErrorBanner :message="store.error" :retryable="showRetry" @retry="onRetry" />

    <div class="game-tools">
      <button class="btn btn-small" type="button" :disabled="busy" @click="onRefresh">
        刷新版本链
      </button>
      <span class="muted">
        手动刷新 = GET /games/{id} + GET /view；页面无自动轮询、无自动重试（D-M6-6④）。
      </span>
    </div>

    <TurnTextStream :text="store.lastText" />

    <div class="game-columns">
      <StatusPanel :view="store.view" />
      <CommandInput :disabled="!canSubmit" @submit="onSubmit" />
    </div>
  </section>
</template>
