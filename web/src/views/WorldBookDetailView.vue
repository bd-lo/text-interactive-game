<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import WorldBookEntryTable from '@/components/WorldBookEntryTable.vue'
import { vReveal } from '@/directives/reveal'
import { useGameStore } from '@/stores/game'
import { useWorldBooksStore } from '@/stores/worldBooks'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'
import { formatDateTime } from '@/utils/labels'
import { labelWorldBookSource } from '@/utils/worldBooks'

const route = useRoute()
const router = useRouter()
const store = useWorldBooksStore()
const game = useGameStore()

const worldBookId = computed<string>(() => String(route.params.worldBookId ?? ''))
const showRetry = computed<boolean>(() => store.detailError !== null)
const createError = ref<PlayerErrorInfo | null>(null)

async function load(): Promise<void> {
  const id = worldBookId.value
  if (id === '') {
    return
  }
  try {
    await store.loadDetail(id)
  } catch {
    // 404 等已转成玩家可读错误，由 ErrorBanner 展示
  }
}

async function onStartGame(): Promise<void> {
  createError.value = null
  store.disarmDelete()
  try {
    const id = await game.createGame({
      scenario: 'story',
      world_book_id: worldBookId.value,
    })
    await router.push({ name: 'game', params: { id } })
  } catch (error) {
    createError.value = describeError(error)
  }
}

async function onDelete(): Promise<void> {
  if (!store.deleteArmed) {
    store.armDelete()
    return
  }
  const removed = await store.remove(worldBookId.value)
  if (removed) {
    await router.push({ name: 'world-books' })
  }
}

onMounted(() => {
  void load()
})

watch(worldBookId, () => {
  store.disarmDelete()
  void load()
})
</script>

<template>
  <section class="world-book-detail-view">
    <header class="game-head">
      <h2 class="world-books-title">
        {{ store.detail?.name ?? '世界书' }}
      </h2>
      <p v-if="store.detail !== null" class="game-meta">
        {{ labelWorldBookSource(store.detail.source) }} · {{ store.detail.entry_count }} 条设定 ·
        更新于 {{ formatDateTime(store.detail.updated_at) }}
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'world-books' }">
        ← 返回世界书
      </RouterLink>
      <button class="btn" type="button" :disabled="store.detail === null || game.busy" @click="onStartGame">
        {{ game.busy ? '正在开局…' : '用这本世界书开局' }}
      </button>
      <button
        v-if="!store.deleteArmed"
        class="btn btn-small btn-secondary"
        type="button"
        :disabled="store.deleteBusy || store.detail === null"
        @click="onDelete"
      >
        删除
      </button>
      <template v-else>
        <button class="btn btn-small" type="button" :disabled="store.deleteBusy" @click="onDelete">
          {{ store.deleteBusy ? '删除中…' : '确认删除（不可恢复）' }}
        </button>
        <button class="btn btn-small btn-secondary" type="button" @click="store.disarmDelete()">
          取消
        </button>
      </template>
    </div>

    <ErrorBanner
      :title="store.detailError?.title"
      :message="store.detailError?.message ?? ''"
      :detail="store.detailError?.detail"
      :retryable="showRetry"
      @retry="load"
    />
    <ErrorBanner
      :title="createError?.title"
      :message="createError?.message ?? ''"
      :detail="createError?.detail"
    />
    <ErrorBanner
      :title="store.deleteError?.title"
      :message="store.deleteError?.message ?? ''"
      :detail="store.deleteError?.detail"
    />

    <p v-if="store.detailLoading" class="muted" role="status">正在读取世界书…</p>
    <template v-else-if="store.detail !== null">
      <section v-if="store.detail.description !== ''" v-reveal class="panel">
        <h3 class="panel-title">简介</h3>
        <p class="pre-text">{{ store.detail.description }}</p>
      </section>
      <WorldBookEntryTable v-reveal :entries="store.entries" />
    </template>
    <p v-else-if="store.detailError === null" class="muted" role="status">
      没有找到这本世界书，可能已被删除。
    </p>
  </section>
</template>
