<script setup lang="ts">
/**
 * 生成人物卡视图（M6-028，路由 `/cards/generate`；第 23 章 §23.6 F4）。
 *
 * 页面结构：显著离线提示 → brief 表单 → 生成失败提示 → 草稿预览（可编辑/重新校验/保存）
 * → 保存成功区（显式「查看卡片」/「按此卡开局」；保存前绝不自动建局或跳转）。
 */
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import CardDraftPreview from '@/components/CardDraftPreview.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import GenerateCardForm from '@/components/GenerateCardForm.vue'
import { useCardGenerateStore } from '@/stores/cardGenerate'
import { useGameStore } from '@/stores/game'

const router = useRouter()
const store = useCardGenerateStore()
const game = useGameStore()

const generateErrorRetryable = computed<boolean>(
  () => store.error !== '' && !store.busy && !store.saveBusy,
)

async function onGenerate(): Promise<void> {
  await store.generate()
}

async function onStartGame(): Promise<void> {
  if (store.savedCardId === '') {
    return
  }
  try {
    // 只有玩家显式点「按此卡开局」才建局/跳转（第 23 章 §23.6-6）。
    const gameId = await game.createGame({ card_id: store.savedCardId })
    await router.push({ name: 'game', params: { id: gameId } })
  } catch {
    // 错误已入 game.error，由成功区下方 ErrorBanner 展示
  }
}
</script>

<template>
  <section class="generate-card-view" data-testid="generate-card-page">
    <header class="game-head">
      <h2>生成人物卡</h2>
      <p class="game-meta">写一句人物设定，生成一张可玩的人物卡草稿；确认后保存到卡库。</p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'cards' }">← 返回卡库</RouterLink>
    </div>

    <!-- 显著离线提示（M6B-4 已知限制）：默认离线 = 确定性模板兜底，必须让玩家先看到。 -->
    <section
      class="panel offline-hint"
      data-testid="offline-hint"
      role="note"
      aria-label="离线生成说明"
    >
      <h3 class="panel-title">当前默认离线：生成结果为模板草稿</h3>
      <p>
        默认部署<strong>未启用 LLM</strong>：点「生成人物卡」会立即用确定性模板兜底，得到一张
        <strong>「模板草稿」</strong>（来源=手动、生成者=无）。<strong>配置 LLM 后才是 LLM 生成</strong>。
      </p>
      <p class="muted">
        无论哪种生成，草稿都不会自动保存或开局；生成限流每分钟 5 次，失败请手动重试（不会自动重试）。
      </p>
    </section>

    <GenerateCardForm @submit="onGenerate" />

    <ErrorBanner
      data-testid="generate-error"
      class="generate-error"
      :title="store.errorTitle"
      :message="store.error"
      :detail="store.errorDetail"
      :retryable="generateErrorRetryable"
      retry-label="重试"
      @retry="onGenerate"
    />

    <CardDraftPreview />

    <section
      v-if="store.savedCardId !== ''"
      class="panel save-result"
      data-testid="save-result"
      role="status"
    >
      <h3 class="panel-title">已保存到卡库</h3>
      <p class="muted">
        人物卡 ID：{{ store.savedCardId }} · 保存后不会自动开局，请选择下一步。
      </p>
      <div class="game-tools">
        <RouterLink
          class="btn btn-small"
          data-testid="open-card"
          :to="{ name: 'card-detail', params: { cardId: store.savedCardId } }"
        >
          查看卡片
        </RouterLink>
        <button
          class="btn btn-small"
          type="button"
          data-testid="start-game-with-card"
          :disabled="game.busy"
          @click="onStartGame"
        >
          {{ game.busy ? '创建中…' : '按此卡开局' }}
        </button>
      </div>
      <ErrorBanner :title="game.errorTitle" :message="game.error" :detail="game.errorDetail" />
    </section>
  </section>
</template>
