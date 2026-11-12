<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getScenario } from '@/api/client'
import ActionChips from '@/components/ActionChips.vue'
import CommandInput from '@/components/CommandInput.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import GoalPanel from '@/components/GoalPanel.vue'
import NarrativeHistory from '@/components/NarrativeHistory.vue'
import StatusPanel from '@/components/StatusPanel.vue'
import TurnFeedback from '@/components/TurnFeedback.vue'
import TurnTextStream from '@/components/TurnTextStream.vue'
import WorldLorePanel from '@/components/WorldLorePanel.vue'
import { vReveal } from '@/directives/reveal'
import { useGameStore } from '@/stores/game'
import { useVisualStore } from '@/stores/visual'
import { IS_DEV } from '@/utils/env'
import { displayHistory, lastResponseText } from '@/utils/history'
import { formatEndingSummary, labelGameStatus, labelScenario } from '@/utils/labels'
import { isAppAction, suggestActions } from '@/utils/suggestions'

const route = useRoute()
const store = useGameStore()
const visual = useVisualStore()

const gameId = computed<string>(() => String(route.params.id ?? ''))
const busy = computed<boolean>(() => store.busy)
const canSubmit = computed<boolean>(() => store.canSubmit)
const ended = computed<boolean>(() => store.status === 'ended')
const showRetry = computed<boolean>(() => store.canRetry)
const retryLabel = computed<string>(() => {
  if (store.errorCode === 'CONFLICT') {
    return '重新发送'
  }
  // 流式失败/取消后的手动重试显式走普通 /turns（不再次尝试流式，避免重复请求）。
  return store.retrySyncOnly ? '重试（同步）' : '重试'
})
const roomTitle = computed<string>(() => store.view?.room.title ?? '冒险')
const goal = computed(() => store.view?.goal ?? null)
const endingText = computed<string>(() => store.view?.ending_text ?? '')

/**
 * 自定义场景 id：本会话建局从 store 拿；刷新/深链从 `/game/:id?scenario_id=scn_...` 拿
 * （`/view.scenario` 只有字符串 `"custom"`，不带 id，见场景设计 §3.2/§3.5）。
 */
const customScenarioId = computed<string>(() => {
  if (store.scenario !== 'custom') {
    return ''
  }
  if (store.customScenarioId !== '') {
    return store.customScenarioId
  }
  const fromQuery = route.query.scenario_id
  return typeof fromQuery === 'string' ? fromQuery : ''
})
/** 自定义场景标题（详情端点取；取不到不打扰玩家：徽章退回「自定义场景」）。 */
const customScenarioTitle = ref('')

async function loadCustomScenarioTitle(scenarioId: string): Promise<void> {
  customScenarioTitle.value = ''
  if (scenarioId === '') {
    return
  }
  try {
    const detail = await getScenario(scenarioId)
    customScenarioTitle.value = detail.title
  } catch {
    // 场景可能已软删或后端不可用：只影响徽章文案，不影响对局本身
  }
}

/** 场景名徽章：内置场景用固定中文名，自定义场景用场景详情标题（缺省「自定义场景」）。 */
const scenarioLabel = computed<string>(() => {
  if (store.scenario === '') {
    return ''
  }
  if (store.scenario !== 'custom') {
    return labelScenario(store.scenario)
  }
  return customScenarioTitle.value === ''
    ? '自定义场景'
    : `自定义场景：${customScenarioTitle.value}`
})
/** R3 来源徽章修正：空来源（如仅做过看/背包等无事件回合）不再误标「离线模板」，显示中性文案。 */
const sourceLabel = computed<string>(() => {
  if (store.narrativeSource === 'llm') {
    return 'LLM 叙事'
  }
  if (store.narrativeSource === 'template') {
    return '离线模板'
  }
  return '来源未知'
})
/** AC-34 结局屏通关总结：「本局 N 回合 · 目标 X/Y · 生命值 HP/MAX」（数据源 /view）。 */
const endingSummary = computed<string>(() => {
  const view = store.view
  if (view === null) {
    return ''
  }
  return formatEndingSummary({
    turn: view.turn,
    hp: view.hp,
    maxHp: view.max_hp,
    goal: goal.value,
  })
})
/**
 * R3 冒险记录展示序列：空局插「开场」/ ended 追加「结局」；纯派生、重复计算幂等，
 * 不改 store（刷新重建 replay 后依然成立）。
 */
const historyEntries = computed(() =>
  displayHistory(store.history, {
    view: store.view,
    status: store.status,
    endingText: store.view?.ending_text,
  }),
)

/**
 * R-23 配套「最近回应」：零事件只读回合不写入冒险记录，正文只在 `store.lastText`
 * （快照 `last_text` 刷新后可恢复）。事件回合已进 history → 不重复展示。
 */
const recentResponse = computed<string>(() =>
  lastResponseText(store.lastText, historyEntries.value),
)

/** 服务端建议里混有「刷新对局视图」这类应用动作，不能当游戏指令提交。 */
function gameCommands(actions: readonly string[]): string[] {
  return actions.filter((action) => action !== '' && !isAppAction(action))
}

/** 失败回合自带建议（TurnResult，仅失败时非空；应用动作已过滤）。 */
const turnActions = computed<string[]>(() => {
  return gameCommands(store.lastSuggestedActions)
})
/** `/view` 的「下一步」建议（成功回合也给，D9 加法字段）。 */
const viewActions = computed<string[]>(() => {
  return gameCommands(store.view?.suggested_actions ?? [])
})
/** 从 /view 派生的确定性建议（后端没给建议时的兜底）。 */
const derivedActions = computed<string[]>(() => suggestActions(store.view))
const suggestionActions = computed<string[]>(() =>
  viewActions.value.length > 0 ? viewActions.value : derivedActions.value,
)
/** 建议面板：新手引导/失败回合自带建议/结局后都不重复展示「可以试试」。 */
const showSuggestionPanel = computed<boolean>(
  () =>
    store.status === 'active' &&
    !showOnboarding.value &&
    turnActions.value.length === 0 &&
    suggestionActions.value.length > 0,
)
const showOnboarding = computed<boolean>(
  () =>
    store.view !== null &&
    store.status === 'active' &&
    !store.historyLoading &&
    store.history.length === 0,
)

async function ensureLoaded(): Promise<void> {
  const id = gameId.value
  if (id === '') {
    return
  }
  if (
    store.gameId === id &&
    store.version > 0 &&
    store.view !== null &&
    store.view.game_id === id
  ) {
    return
  }
  try {
    await store.loadGame(id)
  } catch {
    // 错误已写入 store.error*（如 404）
  }
}

async function onRefresh(): Promise<void> {
  try {
    await store.refreshAll()
    await store.loadHistory({ merge: true })
  } catch {
    // 保留 store.error*
  }
}

async function onSubmit(text: string): Promise<void> {
  await store.submitTurn(text)
}

async function onCommand(text: string): Promise<void> {
  if (isAppAction(text)) {
    await onRefresh()
    return
  }
  await onSubmit(text)
}

async function onRetry(): Promise<void> {
  await store.retry()
}

/** 取消流式显示（不撤销服务端已/将提交的回合；busy 由 store 收尾释放）。 */
function onCancelStream(): void {
  store.cancelStream()
}

onMounted(() => {
  void ensureLoaded()
  // 流式叙事阶段 1（N2-039）：只有服务端 LLM 已启用才尝试流式；失败静默走同步（离线路径零变化）。
  void store.refreshStreamingAvailability()
})

watch(gameId, () => {
  void ensureLoaded()
})

watch(
  customScenarioId,
  (scenarioId) => {
    void loadCustomScenarioTitle(scenarioId)
  },
  { immediate: true },
)
</script>

<template>
  <section class="game-view">
    <header class="game-head">
      <div class="game-title-row">
        <h2>{{ roomTitle }}</h2>
        <span v-if="scenarioLabel !== ''" class="scenario-badge" :title="`场景：${scenarioLabel}`">
          {{ scenarioLabel }}
        </span>
        <span class="source-badge" :title="`叙事来源：${sourceLabel}`">{{ sourceLabel }}</span>
      </div>
      <p class="game-meta">
        第 {{ store.turn }} 回合 · HP {{ store.hpLabel }} · {{ labelGameStatus(store.status) }}
      </p>
    </header>

    <ErrorBanner
      :title="store.errorTitle"
      :message="store.error"
      :detail="store.errorDetail"
      :retryable="showRetry"
      :retry-label="retryLabel"
      @retry="onRetry"
    />

    <!-- 结局屏不再做 live region：结局正文已由「冒险记录」（role=log）追加播报，避免同一段文本被播报两次。 -->
    <section v-if="ended" v-reveal class="panel ending-panel" role="region" aria-labelledby="ending-title">
      <h3 id="ending-title" class="panel-title ending-title" data-text="故事结束">故事结束</h3>
      <p v-if="endingSummary !== ''" class="ending-summary">{{ endingSummary }}</p>
      <p class="ending-text">{{ endingText === '' ? '这一局已经走到结局。' : endingText }}</p>
      <RouterLink class="btn btn-small" :to="{ name: 'home' }">开始新的冒险</RouterLink>
    </section>

    <p v-if="busy && store.view === null" class="muted" role="status">正在载入对局…</p>
    <p v-else-if="!busy && store.view === null && store.error === ''" class="muted" role="status">
      对局还没有载入。
      <RouterLink :to="{ name: 'home' }">回首页</RouterLink>
    </p>

    <div class="game-tools">
      <button class="btn btn-small btn-secondary" type="button" :disabled="busy" @click="onRefresh">
        刷新
      </button>
      <RouterLink
        class="btn btn-small btn-secondary"
        :to="{ name: 'replay', params: { id: gameId } }"
      >
        回放
      </RouterLink>
    </div>

    <GoalPanel v-if="goal !== null" v-reveal :goal="goal" />

    <div v-reveal class="game-columns">
      <div class="game-main">
        <!-- N2-039 流式叙事：仅 LLM 已启用时出现；取消/失败后不残留半截文本（文本区在 store 侧清空）。 -->
        <section
          v-if="store.streaming"
          class="panel stream-panel"
          aria-label="正在生成的叙事"
          aria-busy="true"
          data-testid="stream-panel"
        >
          <div class="stream-head">
            <h3 class="panel-title">正在生成</h3>
            <button
              class="btn btn-small btn-secondary"
              type="button"
              data-testid="stream-cancel"
              @click="onCancelStream"
            >
              取消流式显示
            </button>
          </div>
          <p class="muted stream-status" role="status">
            叙事正在流式生成，可随时取消；取消不撤销本回合（服务端可能已完成）。
          </p>
          <TurnTextStream :text="store.streamText" streaming />
        </section>
        <p v-if="store.streamNotice !== ''" class="muted stream-notice" role="status">
          {{ store.streamNotice }}
        </p>

        <section v-if="showOnboarding" class="panel guide-panel" aria-label="新手引导">
          <h3 class="panel-title">从这里开始</h3>
          <p>
            用一句话告诉你的角色要做什么，比如「环顾四周」「拿起火把」「向北走」。
            下面这些是你现在就能做的事：
          </p>
          <ActionChips :actions="suggestionActions" @command="onCommand" />
        </section>

        <NarrativeHistory
          :entries="historyEntries"
          :loading="store.historyLoading"
          :typewriter="visual.typewriterEnabled"
        />
        <p v-if="store.historyError !== ''" class="muted" role="status">{{ store.historyError }}</p>

        <!-- R-23 配套：零事件只读回合（看/背包）不写冒险记录，正文在这里可见。 -->
        <section
          v-if="recentResponse !== ''"
          class="panel last-response"
          aria-label="最近回应"
          aria-live="polite"
        >
          <h3 class="panel-title">最近回应</h3>
          <pre class="last-response-text">{{ recentResponse }}</pre>
        </section>

        <TurnFeedback
          :effects="store.lastEffects"
          :suggested-actions="turnActions"
          @command="onCommand"
        />

        <section
          v-if="showSuggestionPanel"
          class="panel suggestions-panel"
          aria-label="可以试试"
        >
          <h3 class="panel-title">可以试试</h3>
          <ActionChips :actions="suggestionActions" @command="onCommand" />
        </section>

        <CommandInput :disabled="!canSubmit" :last-command="store.lastInput" @submit="onSubmit" />
        <p v-if="ended" class="muted">这一局已经结束，可以回首页开始新的冒险。</p>
      </div>

      <div class="game-side">
        <WorldLorePanel
          :world-book="store.view?.world_book ?? null"
          :lore="store.view?.lore ?? null"
        />
        <StatusPanel
          :view="store.view"
          :loading="busy"
          :disabled="ended"
          @command="onCommand"
        />
      </div>
    </div>

    <details v-if="IS_DEV" class="panel diagnostics">
      <summary>诊断信息（开发用）</summary>
      <dl class="status-grid">
        <dt>对局 ID</dt>
        <dd><code>{{ store.gameId === '' ? gameId : store.gameId }}</code></dd>
        <dt>回合 / 版本</dt>
        <dd>turn {{ store.turn }} · version {{ store.version }}</dd>
        <dt>快照版本</dt>
        <dd>{{ store.snapshotVersion }}</dd>
        <dt>内部状态</dt>
        <dd>{{ store.status }}</dd>
        <dt>场景 / 叙事来源</dt>
        <dd>{{ store.scenario }} / {{ sourceLabel }}</dd>
        <dt>错误码</dt>
        <dd>{{ store.errorCode === '' ? '（无）' : store.errorCode }}</dd>
      </dl>
      <TurnTextStream :text="store.lastText" />
    </details>
  </section>
</template>
