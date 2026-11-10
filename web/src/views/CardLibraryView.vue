<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { useCardsStore } from '@/stores/cards'
import { formatDateTime, labelCardSource } from '@/utils/labels'

const store = useCardsStore()
const searchText = ref(store.query)

const showRetry = computed<boolean>(() => store.error !== '')

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

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="cards-view">
    <header class="game-head">
      <h2>人物卡库</h2>
      <p class="game-meta">
        共 {{ store.total }} 张 · 第 {{ store.page }} / {{ store.totalPages }} 页
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small" :to="{ name: 'card-generate' }">生成人物卡</RouterLink>
      <RouterLink class="btn btn-small" :to="{ name: 'card-import' }">导入卡片</RouterLink>
      <span class="muted">用一句设定生成一张卡、挑现成的卡开始冒险，或从 JSON 导入自己的角色。</span>
    </div>

    <form class="panel cards-tools" @submit.prevent="onSearch">
      <label class="field">
        <span>搜索名称</span>
        <input v-model="searchText" class="input" type="text" maxlength="100" placeholder="例如：灰烬" :disabled="store.loading" />
      </label>
      <button class="btn" type="submit" :disabled="store.loading">搜索</button>
      <button class="btn btn-secondary" type="button" :disabled="store.loading" @click="load">刷新</button>
    </form>

    <ErrorBanner :message="store.error" :retryable="showRetry" @retry="load" />

    <p v-if="store.loading" class="muted" role="status">正在加载人物卡…</p>
    <p v-else-if="store.items.length === 0 && store.error === ''" class="muted" role="status">
      暂无人物卡。可以先
      <RouterLink :to="{ name: 'card-generate' }">生成一张人物卡</RouterLink>
      ，或
      <RouterLink :to="{ name: 'card-import' }">导入一张 JSON 人物卡</RouterLink>
      。
    </p>

    <ul v-else class="plain-list card-list">
      <li v-for="card in store.items" :key="card.card_id" class="card-list-item panel">
        <div class="card-list-head">
          <RouterLink class="card-name" :to="{ name: 'card-detail', params: { cardId: card.card_id } }">
            {{ card.name }}
          </RouterLink>
          <span class="muted">{{ labelCardSource(card.source) }}</span>
        </div>
        <p class="card-list-meta muted">
          生成者：{{ card.generated_by === null || card.generated_by === '' ? '（无）' : card.generated_by }} ·
          更新：{{ formatDateTime(card.updated_at) }}
        </p>
      </li>
    </ul>

    <div class="game-tools">
      <button class="btn btn-small btn-secondary" type="button" :disabled="!store.canPrev" @click="onPrev">
        ← 上一页
      </button>
      <button class="btn btn-small btn-secondary" type="button" :disabled="!store.canNext" @click="onNext">
        下一页 →
      </button>
      <span class="muted">手动翻页/搜索；无自动轮询、无自动重试。</span>
    </div>
  </section>
</template>
