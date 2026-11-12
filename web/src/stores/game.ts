/**
 * 对局状态 store（M6-025 / R2 包 A/B/C）。
 *
 * 一致性口径（第 23 章 §23.3、D-M6-6⑤）：`version` 只来自服务端响应；本地永不递增。
 * - 回合成功：用 `TurnResult.version` 覆盖，再以 `GET /view` 拉权威 turn/version/目标/结局；
 *   若刷新失败，**视为回合已提交**（不提示重试、不重复执行，包 C F3）；
 * - 网络/未知失败：保留幂等键，`canRetry=true`，由用户**手动**重试（禁自动重试）；
 * - 业务失败 `ok=false`：`CONFLICT` 用响应里的最新 `version` 自愈并刷新版本链/历史（包 C F2），
 *   其余失败把可读原因写进 `error*` 并在冒险记录追加系统行（包 A）；
 * - 冒险记录（history）来自只读 replay 重建 + 本会话即时追加（包 A）；
 * - 错误只存**玩家可读**三段（title/message/detail），detail 仅在「诊断」折叠区展示（包 C）。
 */

import { defineStore } from 'pinia'

import {
  ApiClientError,
  TurnStreamBusinessError,
  createGame,
  getGame,
  getLlmSettings,
  getPlayerView,
  getReplay,
  streamTurn as streamTurnApi,
  submitTurn as submitTurnApi,
} from '@/api/client'
import type {
  CreateGameRequest,
  CreatedGameResponse,
  EventRecord,
  GamePlayerViewResponse,
  GameStatus,
  GameViewResponse,
  ReplayEvent,
  TurnResult,
  TurnStreamEvent,
} from '@/api/types'
import { describeError, describeTurnFailure } from '@/utils/errors'
import {
  appendNarrative,
  appendNotice,
  historyFromReplay,
  mergeReplayHistory,
  type HistoryEntry,
} from '@/utils/history'
import { clear as clearIdempotencyKey, getOrCreate } from '@/utils/idempotency'
import { shouldStreamNarrative } from '@/utils/llm'
import { rememberRecentGame } from '@/utils/recentGames'

/** replay 分页上限（服务端 `limit` 1..500）；历史重建按页拉满后继续。 */
const HISTORY_PAGE_SIZE = 500
/** 历史重建最多翻页数（500×10 条事件足够覆盖长时间存档，防御恶意大档）。 */
const HISTORY_MAX_PAGES = 10
/** 流式正文显示上限（服务端已限 150+64；防御异常长流打爆 DOM/内存）。 */
const STREAM_TEXT_MAX = 4000

/**
 * 正在进行的流式回合取消器（模块级，不放进 Pinia state 以免 devtools 序列化异常）。
 * 取消只停止**显示**；服务端仍会完成提交（D-N2-5：断连不撤销）。
 */
let activeStreamController: AbortController | null = null

/** 叙事来源（与 `TurnResult.events[].result.narrative_source` 契约一致）。 */
export type NarrativeSource = 'llm' | 'template' | ''

/** 从事件里取最近一条 `narrative_source`；没有则返回空串（沿用调用方默认）。 */
function narrativeSourceFromEvents(events: readonly EventRecord[]): NarrativeSource {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const value = events[index]?.result.narrative_source
    if (value === 'llm' || value === 'template') {
      return value
    }
  }
  return ''
}

/**
 * 优先读 TurnResult 的加法字段 `narrative_source`（H4 L3：仅做「看/背包」等无事件回合也能标来源）；
 * 旧服务端/字段缺失时回退到事件里的 `result.narrative_source`（保持向后兼容）。
 */
function narrativeSourceFromResult(result: TurnResult): NarrativeSource {
  const value = result.narrative_source
  if (value === 'llm' || value === 'template') {
    return value
  }
  return narrativeSourceFromEvents(result.events)
}

/** 从 replay 事件里取最近一条 `narrative_source`（刷新/深链后恢复徽章用）。 */
function narrativeSourceFromReplay(events: readonly ReplayEvent[]): NarrativeSource {  const ordered = [...events].sort((left, right) => left.seq - right.seq)
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const value = ordered[index]?.result.narrative_source
    if (value === 'llm' || value === 'template') {
      return value
    }
  }
  return ''
}

interface GameState {
  gameId: string
  turn: number
  version: number
  snapshotVersion: number
  status: GameStatus
  scenario: string
  /** 自定义场景 id（仅本会话建局时已知；对局页徽章取标题用；内置场景为空串）。 */
  customScenarioId: string
  /** 建局种子（仅创建时已知；继续已有对局时从本机最近记录兜底）。 */
  seed: number | null
  cardId: string | null
  lastText: string
  /** 冒险记录（回合日志）：replay 重建 + 本会话即时追加；失败回合追加系统行。 */
  history: HistoryEntry[]
  historyLoading: boolean
  /** 历史加载失败提示（非致命：不影响继续游戏）。 */
  historyError: string
  /** 叙事来源徽章：`llm` / `template` / 空（空 = 来源未知，如仅做过看/背包等无事件回合，前端显示中性文案）。 */
  narrativeSource: NarrativeSource
  /** 失败回合的结构化提示（`effects[].hint`）与建议动作；只读展示，不改 text。 */
  lastEffects: Array<Record<string, unknown>>
  lastSuggestedActions: string[]
  view: GamePlayerViewResponse | null
  busy: boolean
  /** 玩家可读错误（标题/正文/技术细节分开；detail 只在诊断折叠区展示）。 */
  errorTitle: string
  error: string
  errorDetail: string
  errorCode: string
  canRetry: boolean
  lastInput: string
  pendingKey: string | null
  /** 流式叙事阶段 1（N2-039）：LLM 已启用时才尝试流式；失败降级 `/turns` 一次（不自动重试）。 */
  streamingAvailable: boolean
  streaming: boolean
  /** 流式进行中的显示文本（delta 追加 / replace 覆盖；done 后用 done.result.text 校准并清空）。 */
  streamText: string
  /** 取消流式显示后的中性提示（不是错误；服务端可能已提交）。 */
  streamNotice: string
  /** 流式已失败并降级同步：手动重试应显式走普通 `/turns`（按钮文案「重试（同步）」）。 */
  retrySyncOnly: boolean
}

function initialState(): GameState {
  return {
    gameId: '',
    turn: 0,
    version: 0,
    snapshotVersion: 0,
    status: 'active',
    scenario: 'm2',
    customScenarioId: '',
    seed: null,
    cardId: null,
    lastText: '',
    history: [],
    historyLoading: false,
    historyError: '',
    narrativeSource: '',
    lastEffects: [],
    lastSuggestedActions: [],
    view: null,
    busy: false,
    errorTitle: '',
    error: '',
    errorDetail: '',
    errorCode: '',
    canRetry: false,
    lastInput: '',
    pendingKey: null,
    streamingAvailable: false,
    streaming: false,
    streamText: '',
    streamNotice: '',
    retrySyncOnly: false,
  }
}

export const useGameStore = defineStore('game', {
  state: initialState,
  getters: {
    hpLabel(state): string {
      return state.view === null ? '-' : `${state.view.hp}/${state.view.max_hp}`
    },
    canSubmit(state): boolean {
      return !state.busy && state.gameId !== '' && state.status === 'active' && state.view !== null
    },
    hasError(state): boolean {
      return state.error !== ''
    },
  },
  actions: {
    /** 清空错误三件套（新动作/刷新时调用）。 */
    clearError(): void {
      this.errorTitle = ''
      this.error = ''
      this.errorDetail = ''
      this.errorCode = ''
      this.canRetry = false
    },
    /** 网络/协议异常 → 玩家可读三段（包 C）。 */
    captureError(error: unknown): void {
      const info = describeError(error)
      this.errorTitle = info.title
      this.error = info.message
      this.errorDetail = info.detail
      this.errorCode = error instanceof ApiClientError ? error.code : 'INTERNAL'
    },
    /** 回合业务失败（HTTP 200 + ok=false）→ 玩家可读三段（包 C）。 */
    captureTurnFailure(code: string | null, text: string): void {
      const info = describeTurnFailure(code, text)
      this.errorTitle = info.title
      this.error = info.message
      this.errorDetail = info.detail
      this.errorCode = code ?? 'INVALID_INPUT'
    },
    /** 把当前对局写进本机「最近对局」（localStorage；存储不可用时静默降级）。 */
    rememberRecent(): void {
      if (this.gameId === '') {
        return
      }
      rememberRecentGame({
        gameId: this.gameId,
        scenario: this.scenario,
        turn: this.turn,
        version: this.version,
        lastText: this.lastText,
        seed: this.seed,
      })
    },
    applyCreated(created: CreatedGameResponse): void {
      this.gameId = created.game_id
      this.turn = created.turn
      this.version = created.version
      this.snapshotVersion = created.snapshot_version
      this.status = 'active'
      this.lastText = ''
      this.history = []
      this.historyLoading = false
      this.historyError = ''
      this.narrativeSource = ''
      this.lastEffects = []
      this.lastSuggestedActions = []
      this.view = null
      this.streaming = false
      this.streamText = ''
      this.streamNotice = ''
      this.retrySyncOnly = false
    },
    /** 切换/载入对局前清空派生状态：加载失败时不得继续展示上一个对局（R-22）。 */
    resetForLoad(): void {
      this.gameId = ''
      this.turn = 0
      this.version = 0
      this.snapshotVersion = 0
      this.status = 'active'
      this.scenario = 'm2'
      this.customScenarioId = ''
      this.seed = null
      this.cardId = null
      this.lastText = ''
      this.history = []
      this.historyLoading = false
      this.historyError = ''
      this.narrativeSource = ''
      this.lastEffects = []
      this.lastSuggestedActions = []
      this.view = null
      this.lastInput = ''
      this.pendingKey = null
      this.streaming = false
      this.streamText = ''
      this.streamNotice = ''
      this.retrySyncOnly = false
    },
    applySnapshot(snapshot: GameViewResponse): void {
      this.gameId = snapshot.game_id
      this.turn = snapshot.turn
      this.version = snapshot.version
      this.snapshotVersion = snapshot.snapshot_version
      this.status = snapshot.status
      this.scenario = snapshot.scenario ?? this.scenario
      this.lastText = snapshot.last_text ?? ''
      this.lastEffects = []
      this.lastSuggestedActions = []
    },
    /** 刷新 `GET /games/{id}` + `GET /view`：服务端权威版本链（并清掉旧错误）。 */
    async refreshAll(): Promise<void> {
      if (this.gameId === '') {
        return
      }
      this.clearError()
      const snapshot = await getGame(this.gameId)
      this.applySnapshot(snapshot)
      await this.refreshView()
    },
    /** 只拉状态面板数据源（D-M6-4：StatusPanel/view 唯一数据源；含目标/结局/建议）。 */
    async refreshView(): Promise<void> {
      if (this.gameId === '') {
        return
      }
      const view = await getPlayerView(this.gameId)
      this.view = view
      this.turn = view.turn
      this.version = view.version
      this.snapshotVersion = view.snapshot_version
      this.status = view.status
      this.scenario = view.scenario ?? this.scenario
    },
    /** 用只读 replay 重建冒险记录（包 A）；失败非致命：只提示，不阻塞继续游戏。 */
    async loadHistory(options: { merge?: boolean } = {}): Promise<void> {
      if (this.gameId === '') {
        return
      }
      this.historyLoading = true
      this.historyError = ''
      const events: ReplayEvent[] = []
      try {
        let fromSeq = 1
        for (let page = 0; page < HISTORY_MAX_PAGES; page += 1) {
          const chunk = await getReplay(this.gameId, { fromSeq, limit: HISTORY_PAGE_SIZE })
          events.push(...chunk.events)
          if (chunk.events.length < HISTORY_PAGE_SIZE) {
            break
          }
          const next = chunk.to_seq + 1
          if (next <= fromSeq) {
            break
          }
          fromSeq = next
        }
        const rebuilt = historyFromReplay(events)
        this.history =
          options.merge === true ? mergeReplayHistory(rebuilt, this.history) : rebuilt
        const source = narrativeSourceFromReplay(events)
        if (source !== '') {
          this.narrativeSource = source
        }
      } catch {
        this.historyError = '冒险记录加载失败，可点「刷新」重试。'
      } finally {
        this.historyLoading = false
      }
    },
    async createGame(options: CreateGameRequest): Promise<string> {
      this.busy = true
      this.clearError()
      // 自定义场景：/view.scenario 恒为 "custom"（服务端只是字符串）；这里先乐观置位，
      // 建局后 refreshView 会用服务端权威值覆盖。
      this.scenario = options.scenario ?? (options.scenario_id !== undefined ? 'custom' : 'm2')
      this.customScenarioId = options.scenario_id ?? ''
      this.seed = options.seed ?? null
      this.cardId = options.card_id ?? null
      // R-24：幂等键由 store 生成「逻辑动作名 → 一次性 UUID」再交给 client 发送。
      // 不能把动作名本身当 Idempotency-Key：同卡/同场景再次开局会命中服务端幂等回放，
      // 拿到上一局而开不出新对局（e2e「详情页按此卡开局」曾因此失败）。
      const actionName =
        `create-game:${options.card_id ?? 'none'}:${options.seed ?? 'default'}` +
        `:${options.scenario ?? 'm2'}:${options.scenario_id ?? 'none'}` +
        `:${options.world_book_id ?? 'none'}`
      const actionKey = getOrCreate(actionName)
      try {
        const created = await createGame(options, actionKey)
        clearIdempotencyKey(actionName)
        this.applyCreated(created)
        try {
          await this.refreshView()
        } catch (error) {
          // 对局已创建成功：只提示刷新失败，不抛错阻塞进入对局（包 C）。
          this.captureError(error)
          this.errorTitle = '对局已创建'
          this.error = '对局已经建好了，但状态没拉取到；进入后点「刷新」即可。'
        }
        this.rememberRecent()
        return created.game_id
      } catch (error) {
        this.captureError(error)
        throw error
      } finally {
        this.busy = false
      }
    },
    async loadGame(gameId: string): Promise<void> {
      this.busy = true
      this.clearError()
      // R-22：先清空上一个对局的 view/history/version，加载失败时页面不会残留旧对局内容。
      this.resetForLoad()
      try {
        this.gameId = gameId
        const snapshot = await getGame(gameId)
        this.applySnapshot(snapshot)
        this.seed = null
        this.cardId = null
        await this.refreshView()
        this.rememberRecent()
        await this.loadHistory()
      } catch (error) {
        this.captureError(error)
        throw error
      } finally {
        this.busy = false
      }
    },
    /**
     * 成功回合后的数据刷新：刷新失败**不得**提示重试（回合已提交，包 C F3）。
     */
    async refreshAfterTurn(): Promise<void> {
      try {
        await this.refreshView()
        this.rememberRecent()
      } catch (error) {
        this.captureError(error)
        this.errorTitle = '回合已保存'
        this.error = '这一回合已经保存成功，但界面数据没刷新出来；点「刷新」重新获取即可（不要重复发送同一指令）。'
        this.canRetry = false
      }
    },
    /**
     * `CONFLICT` 自愈（包 C F2）：业务失败时服务端 200 + ok=false 已回传最新 version；
     * 用它 + 刷新版本链/历史把玩家救回可玩状态，并提示「对局在别处推进了，请重发」。
     */
    async healConflict(serverVersion: number | null, serverText: string): Promise<void> {
      if (serverVersion !== null) {
        this.version = Math.max(this.version, serverVersion)
      }
      this.retrySyncOnly = false
      this.streamNotice = ''
      this.errorCode = 'CONFLICT'
      this.canRetry = false
      try {
        await this.refreshAll()
        await this.loadHistory({ merge: true })
      } catch (error) {
        this.captureError(error)
      }
      this.errorTitle = '对局在别处更新了'
      this.error = `这个对局刚刚在别处（另一个标签页或终端）推进了，已同步到第 ${this.turn} 回合。请重新发送刚才的指令。`
      this.errorDetail = `CONFLICT：${serverText === '' ? '版本过期' : serverText}`
      this.errorCode = 'CONFLICT'
      this.history = appendNotice(this.history, '（对局已在别处推进，这次指令未生效）')
      this.canRetry = true
    },
    /**
     * 探测服务端是否已启用 LLM：只有 `enabled=true` 才尝试流式（ADR D-N2-6）。
     * 读取失败/离线一律回退 false → 完全走既有 `POST /turns`（离线路径零变化）。
     */
    async refreshStreamingAvailability(): Promise<void> {
      try {
        this.streamingAvailable = shouldStreamNarrative(await getLlmSettings())
      } catch {
        this.streamingAvailable = false
      }
    },
    /** 取消正在进行的流式显示：只停止显示，不撤销服务端提交（D-N2-5）；busy 由 submitTurn 收尾释放。 */
    cancelStream(): void {
      if (activeStreamController !== null) {
        activeStreamController.abort()
      }
    },
    /** 流式事件 → 显示文本：`delta` 追加、`replace` 覆盖（`done`/`error` 由调用链处理）。 */
    applyStreamEvent(event: TurnStreamEvent): void {
      if (event.type === 'delta') {
        this.streamText = (this.streamText + event.text).slice(0, STREAM_TEXT_MAX)
      } else if (event.type === 'replace') {
        this.streamText = event.text.slice(0, STREAM_TEXT_MAX)
      }
    },
    /** 成功/业务失败回合的统一收尾（流式 `done.result` 与普通 `/turns` 共用同一口径）。 */
    async handleTurnResult(result: TurnResult, actionName: string): Promise<void> {
      if (result.ok) {
        clearIdempotencyKey(actionName)
        this.pendingKey = null
        this.canRetry = false
        this.retrySyncOnly = false
        this.lastText = result.text
        this.lastEffects = result.effects
        this.lastSuggestedActions = result.suggested_actions
        this.version = result.version
        const source = narrativeSourceFromResult(result)
        if (source !== '') {
          this.narrativeSource = source
        }
        // R-23：冒险记录 = 事件记录。零事件只读回合（看/背包）没有 Event，刷新后 replay
        // 无法重建；因此不写入 history，只更新 lastText，避免刷新前后记录不一致。
        if (result.events.length > 0) {
          this.history = appendNarrative(this.history, {
            text: result.text,
            turnId: result.turn_id,
          })
        }
        await this.refreshAfterTurn()
        return
      }
      // 业务失败：HTTP 200 + ok=false，零写入；同一输入重提视为新逻辑动作。
      clearIdempotencyKey(actionName)
      this.pendingKey = null
      this.lastEffects = result.effects
      this.lastSuggestedActions = result.suggested_actions
      if (result.error_code === 'CONFLICT') {
        await this.healConflict(result.version, result.text)
        return
      }
      this.retrySyncOnly = false
      this.canRetry = false
      this.captureTurnFailure(result.error_code, result.text)
      this.history = appendNotice(this.history)
    },
    /** 传输/协议异常的统一收尾（普通 `/turns` 与流式降级后的同步请求共用）。 */
    async handleTurnError(error: unknown, actionName: string): Promise<void> {
      if (error instanceof ApiClientError && error.isConflict) {
        clearIdempotencyKey(actionName)
        this.pendingKey = null
        await this.healConflict(null, error.message)
      } else {
        // 网络/超时失败：保留幂等键 → 用户手动重试时服务端可回放首次结果。
        this.captureError(error)
        this.canRetry = true
      }
    },
    /** 流式 `error` 事件（回合未提交）：按既有业务失败口径处理，**不降级同步**（避免重复提交）。 */
    async handleStreamBusinessFailure(
      error: TurnStreamBusinessError,
      actionName: string,
    ): Promise<void> {
      clearIdempotencyKey(actionName)
      this.pendingKey = null
      if (error.code === 'CONFLICT') {
        await this.healConflict(null, error.message)
        return
      }
      this.retrySyncOnly = false
      this.canRetry = false
      this.captureTurnFailure(error.code, error.message)
      this.history = appendNotice(this.history)
    },
    /**
     * 尝试流式一次：`true` = 已处理（成功/业务失败/用户取消）；
     * `false` = 传输或协议失败（半截文本已清空），由调用方降级普通 `/turns` **恰好一次**。
     */
    async streamOnce(input: string, actionKey: string, actionName: string): Promise<boolean> {
      this.streaming = true
      this.streamText = ''
      const controller = new AbortController()
      activeStreamController = controller
      try {
        const result = await streamTurnApi(
          { game_id: this.gameId, input, expected_version: this.version },
          actionKey,
          { signal: controller.signal, onEvent: (event) => this.applyStreamEvent(event) },
        )
        await this.handleTurnResult(result, actionName)
        return true
      } catch (error) {
        if (error instanceof ApiClientError && error.code === 'CANCELLED') {
          // 取消：不卡 busy、不自动降级/重试；保留幂等键，手动「重试（同步）」可安全确认结果。
          this.streamText = ''
          this.streamNotice =
            '已取消流式显示。服务端可能仍会完成这一回合：可点「刷新」查看，或点「重试（同步）」确认结果。'
          this.canRetry = true
          this.retrySyncOnly = true
          return true
        }
        if (error instanceof TurnStreamBusinessError) {
          await this.handleStreamBusinessFailure(error, actionName)
          return true
        }
        // 传输/协议/超时：丢弃半截文本；先自动降级同步一次（不提示重试、不循环）。
        this.streamText = ''
        this.retrySyncOnly = true
        return false
      } finally {
        this.streaming = false
        activeStreamController = null
      }
    },
    async submitTurn(input: string, options: { forceSync?: boolean } = {}): Promise<void> {
      const text = input.trim()
      if (this.busy || this.gameId === '' || text === '') {
        return
      }
      this.busy = true
      this.clearError()
      this.lastEffects = []
      this.lastSuggestedActions = []
      this.lastInput = text
      this.streamNotice = ''
      this.retrySyncOnly = false
      this.streamText = ''
      // 同一逻辑动作：手动重试复用 pendingKey；新动作由 version 派生新键。
      const actionName = `turn:${this.gameId}:${this.version}`
      const actionKey = this.pendingKey ?? getOrCreate(actionName)
      this.pendingKey = actionKey
      try {
        const useStream = this.streamingAvailable && options.forceSync !== true
        if (useStream) {
          const settled = await this.streamOnce(text, actionKey, actionName)
          if (settled) {
            return
          }
          // 流式不可用/失败 → 降级普通 /turns **恰好一次**（不是自动重试；失败后由玩家手动重试）。
          this.retrySyncOnly = true
        }
        const result = await submitTurnApi(
          { game_id: this.gameId, input: text, expected_version: this.version },
          actionKey,
        )
        await this.handleTurnResult(result, actionName)
      } catch (error) {
        await this.handleTurnError(error, actionName)
      } finally {
        this.streaming = false
        this.streamText = ''
        activeStreamController = null
        this.busy = false
      }
    },
    /** 用户点「重试」：手动重放上一次输入（不是自动重试；回合已提交时 canRetry=false）。 */
    async retry(): Promise<void> {
      if (!this.canRetry || this.lastInput === '') {
        return
      }
      const forceSync = this.retrySyncOnly
      await this.submitTurn(this.lastInput, { forceSync })
    },
  },
})
