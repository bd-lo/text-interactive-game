<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { vReveal } from '@/directives/reveal'
import { useGameStore } from '@/stores/game'
import { useScenariosStore } from '@/stores/scenarios'
import type { PlayerErrorInfo } from '@/utils/errors'
import { formatDateTime } from '@/utils/labels'
import {
  describeScenarioError,
  formatScenarioIssue,
  formatScenarioStats,
  scenarioDefinitionSections,
} from '@/utils/scenarios'

const route = useRoute()
const router = useRouter()
const store = useScenariosStore()
const game = useGameStore()

const scenarioId = computed<string>(() => String(route.params.scenarioId ?? ''))
const showRetry = computed<boolean>(() => store.detailError !== null)
const createError = ref<PlayerErrorInfo | null>(null)

const detail = computed(() => store.detail)
const sections = computed(() =>
  detail.value === null ? null : scenarioDefinitionSections(detail.value.definition),
)
/** 原始 JSON 折叠区：只读展示玩家上传的定义（`<pre>` 文本插值，禁富文本注入）。 */
const rawJsonText = computed<string>(() =>
  detail.value === null ? '' : JSON.stringify(detail.value.definition, null, 2),
)

async function load(): Promise<void> {
  const id = scenarioId.value
  if (id === '') {
    return
  }
  try {
    await store.loadDetail(id)
  } catch {
    // 404/422 等已转成玩家可读错误，由 ErrorBanner 展示
  }
}

/** 按此场景开局：body `{scenario_id}`；成功后把 id 带进对局页 URL（对局徽章取标题用）。 */
async function onStartGame(): Promise<void> {
  createError.value = null
  store.disarmDelete()
  try {
    const gameId = await game.createGame({ scenario_id: scenarioId.value })
    await router.push({
      name: 'game',
      params: { id: gameId },
      query: { scenario_id: scenarioId.value },
    })
  } catch (error) {
    createError.value = describeScenarioError(error)
  }
}

/** 删除：二次确认（第一次点击只武装）；成功后回场景库。 */
async function onDelete(): Promise<void> {
  if (!store.deleteArmed) {
    store.armDelete()
    return
  }
  const removed = await store.remove(scenarioId.value)
  if (removed) {
    await router.push({ name: 'scenarios' })
  }
}

onMounted(() => {
  void load()
})

watch(scenarioId, () => {
  store.disarmDelete()
  void load()
})
</script>

<template>
  <section class="scenario-detail-view">
    <header class="game-head">
      <h2>{{ detail?.title ?? '场景详情' }}</h2>
      <p v-if="detail !== null" class="game-meta">
        <span v-if="detail.author !== ''">作者 {{ detail.author }} · </span>
        场景 v{{ detail.schema_version }} · slug {{ detail.slug }} ·
        导入于 {{ formatDateTime(detail.created_at) }}
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'scenarios' }">
        ← 返回场景库
      </RouterLink>
      <button
        class="btn"
        type="button"
        :disabled="detail === null || game.busy || store.deleteBusy"
        @click="onStartGame"
      >
        {{ game.busy ? '正在开局…' : '按此场景开局' }}
      </button>
      <button
        v-if="!store.deleteArmed"
        class="btn btn-small btn-secondary"
        type="button"
        :disabled="store.deleteBusy || detail === null"
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

    <p v-if="store.detailLoading" class="muted" role="status">正在读取场景…</p>
    <p v-else-if="detail === null && store.detailError === null" class="muted" role="status">
      没有找到这个场景，可能已被删除。
    </p>

    <template v-else-if="detail !== null">
      <section v-if="detail.description !== ''" v-reveal class="panel">
        <h3 class="panel-title">简介</h3>
        <p class="pre-text">{{ detail.description }}</p>
      </section>

      <section v-reveal class="panel">
        <h3 class="panel-title">统计摘要</h3>
        <dl class="status-grid">
          <dt>房间</dt>
          <dd>{{ detail.summary.rooms }} 个</dd>
          <dt>物品</dt>
          <dd>{{ detail.summary.items }} 件</dd>
          <dt>角色（NPC）</dt>
          <dd>{{ detail.summary.npcs }} 个</dd>
          <dt>目标</dt>
          <dd>{{ detail.summary.objectives }} 个</dd>
          <dt>结局</dt>
          <dd>{{ detail.summary.endings }} 个</dd>
          <dt>建议动作</dt>
          <dd>{{ detail.summary.suggestions }} 条</dd>
          <dt>场景 ID</dt>
          <dd><code>{{ detail.scenario_id }}</code></dd>
        </dl>
      </section>

      <section v-if="detail.warnings.length > 0" v-reveal class="panel">
        <h3 class="panel-title">导入提示（不阻断游玩）</h3>
        <ul class="plain-list scenario-issue-list">
          <li v-for="(item, index) in detail.warnings" :key="`${item.path}-${index}`">
            {{ formatScenarioIssue(item) }}
          </li>
        </ul>
      </section>

      <template v-if="sections !== null">
        <section v-reveal class="panel scenario-section">
          <h3 class="panel-title">房间（{{ sections.rooms.length }}）</h3>
          <ul v-if="sections.rooms.length > 0" class="plain-list scenario-text-list">
            <li v-for="room in sections.rooms" :key="room.key">
              <span class="scenario-entry-name">{{ room.name }}</span>
              <span v-if="room.description !== ''" class="muted"> — {{ room.description }}</span>
            </li>
          </ul>
          <p v-else class="muted">这份场景没有房间。</p>
        </section>

        <section v-reveal class="panel scenario-section">
          <h3 class="panel-title">物品（{{ sections.items.length }}）</h3>
          <ul v-if="sections.items.length > 0" class="plain-list scenario-text-list">
            <li v-for="item in sections.items" :key="item.key">
              <span class="scenario-entry-name">{{ item.name }}</span>
              <span v-if="item.description !== ''" class="muted"> — {{ item.description }}</span>
            </li>
          </ul>
          <p v-else class="muted">这份场景没有物品。</p>
        </section>

        <section v-reveal class="panel scenario-section">
          <h3 class="panel-title">角色（{{ sections.npcs.length }}）</h3>
          <ul v-if="sections.npcs.length > 0" class="plain-list scenario-text-list">
            <li v-for="npc in sections.npcs" :key="npc.key">
              <span class="scenario-entry-name">{{ npc.name }}</span>
              <span v-if="npc.roomName !== ''" class="muted"> — 在{{ npc.roomName }}</span>
            </li>
          </ul>
          <p v-else class="muted">这份场景没有角色。</p>
        </section>

        <section v-reveal class="panel scenario-section">
          <h3 class="panel-title">目标</h3>
          <template v-if="sections.goal !== null">
            <p v-if="sections.goal.title !== ''" class="scenario-goal-title">
              {{ sections.goal.title }}
            </p>
            <p v-if="sections.goal.description !== ''" class="muted">
              {{ sections.goal.description }}
            </p>
            <ol v-if="sections.goal.objectives.length > 0" class="plain-list scenario-objective-list">
              <li v-for="(objective, index) in sections.goal.objectives" :key="`${objective.key}-${index}`">
                {{ objective.text }}
              </li>
            </ol>
          </template>
          <p v-else class="muted">这份场景没有目标面板（按内置兜底规则结算结局）。</p>
        </section>

        <section v-reveal class="panel scenario-section">
          <h3 class="panel-title">结局（{{ sections.endings.length }}）</h3>
          <ul v-if="sections.endings.length > 0" class="plain-list scenario-text-list">
            <li v-for="ending in sections.endings" :key="ending.key">
              <span class="world-tag">{{ ending.kindLabel }}</span>
              <span class="scenario-entry-name">{{ ending.title }}</span>
              <span v-if="ending.text !== ''" class="muted"> — {{ ending.text }}</span>
            </li>
          </ul>
          <p v-else class="muted">这份场景没有自定义结局（按内置兜底规则结算）。</p>
        </section>
      </template>

      <p class="muted">摘要：{{ formatScenarioStats(detail.summary) }}。</p>

      <details v-reveal class="panel scenario-raw">
        <summary>原始 JSON（只读，导入时的定义原文）</summary>
        <pre class="pre-text scenario-raw-json">{{ rawJsonText }}</pre>
      </details>
    </template>
  </section>
</template>
