<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { vReveal } from '@/directives/reveal'
import { useWorldBooksStore } from '@/stores/worldBooks'
import { formatDateTime } from '@/utils/labels'
import { labelWorldBookSource } from '@/utils/worldBooks'

const store = useWorldBooksStore()
const searchText = ref(store.query)
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

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="world-books-view">
    <header class="game-head">
      <h2 class="world-books-title">世界书</h2>
      <p class="game-meta">
        共 {{ store.total }} 本 · 第 {{ store.page }} / {{ store.totalPages }} 页
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small" :to="{ name: 'world-book-import' }">导入世界书</RouterLink>
      <span class="muted">世界书给故事补充设定；开新局时可选择一本。</span>
    </div>

    <form v-reveal class="panel cards-tools" @submit.prevent="onSearch">
      <label class="field">
        <span>搜索名称</span>
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

    <p v-if="store.loading" class="muted" role="status">正在读取世界书…</p>
    <p v-else-if="store.items.length === 0 && store.error === null" class="muted" role="status">
      还没有世界书。可以先
      <RouterLink :to="{ name: 'world-book-import' }">导入一本</RouterLink>
      ，开新局时选择它来补充世界设定。
    </p>

    <ul v-else class="plain-list world-book-list">
      <li v-for="book in store.items" :key="book.world_book_id" v-reveal class="panel world-book-item">
        <div class="world-book-head">
          <RouterLink
            class="world-book-name"
            :to="{ name: 'world-book-detail', params: { worldBookId: book.world_book_id } }"
          >
            {{ book.name }}
          </RouterLink>
          <span class="world-tag">{{ labelWorldBookSource(book.source) }}</span>
        </div>
        <p class="world-book-meta muted">
          {{ book.entry_count }} 条设定 · 更新于 {{ formatDateTime(book.updated_at) }}
        </p>
        <p v-if="book.description !== ''" class="world-book-desc">{{ book.description }}</p>
        <div class="game-tools world-book-actions">
          <RouterLink
            class="btn btn-small btn-secondary"
            :to="{ name: 'world-book-detail', params: { worldBookId: book.world_book_id } }"
          >
            查看条目
          </RouterLink>
        </div>
      </li>
    </ul>

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
