/**
 * 对局状态 store（M6-025）。
 *
 * 一致性口径（第 23 章 §23.3、D-M6-6⑤）：`version` 只来自服务端响应；本地永不递增。
 * - 回合成功：用 `TurnResult.version` 覆盖，再以 `GET /view` 拉权威 turn/version；
 * - 网络/未知失败：保留幂等键，`canRetry=true`，由用户**手动**重试（禁自动重试，D-M6-6④）；
 * - `CONFLICT`：清键 → 刷新 `GET /games/{id}` 的版本链 → 用户重提时用新键（新逻辑动作）。
 */

import { defineStore } from 'pinia'

import { ApiClientError, createGame, getGame, getPlayerView, submitTurn as submitTurnApi } from '@/api/client'
import type {
  CreateGameRequest,
  CreatedGameResponse,
  GamePlayerViewResponse,
  GameStatus,
  GameViewResponse,
} from '@/api/types'
import { clear as clearIdempotencyKey, getOrCreate } from '@/utils/idempotency'

interface GameState {
  gameId: string
  turn: number
  version: number
  snapshotVersion: number
  status: GameStatus
  lastText: string
  /** 失败回合的结构化提示（`effects[].hint`）与建议动作；只读展示，不改 text。 */
  lastEffects: Array<Record<string, unknown>>
  lastSuggestedActions: string[]
  view: GamePlayerViewResponse | null
  busy: boolean
  error: string
  errorCode: string
  canRetry: boolean
  lastInput: string
  pendingKey: string | null
}

function initialState(): GameState {
  return {
    gameId: '',
    turn: 0,
    version: 0,
    snapshotVersion: 0,
    status: 'active',
    lastText: '',
    lastEffects: [],
    lastSuggestedActions: [],
    view: null,
    busy: false,
    error: '',
    errorCode: '',
    canRetry: false,
    lastInput: '',
    pendingKey: null,
  }
}

export const useGameStore = defineStore('game', {
  state: initialState,
  getters: {
    hpLabel(state): string {
      return state.view === null ? '-' : `${state.view.hp}/${state.view.max_hp}`
    },
    canSubmit(state): boolean {
      return !state.busy && state.gameId !== '' && state.status === 'active'
    },
  },
  actions: {
    captureError(error: unknown): void {
      if (error instanceof ApiClientError) {
        this.error = `${error.code}：${error.message}`
        this.errorCode = error.code
        return
      }
      this.error = error instanceof Error ? error.message : String(error)
      this.errorCode = 'INTERNAL'
    },
    applyCreated(created: CreatedGameResponse): void {
      this.gameId = created.game_id
      this.turn = created.turn
      this.version = created.version
      this.snapshotVersion = created.snapshot_version
      this.status = 'active'
      this.lastText = ''
      this.lastEffects = []
      this.lastSuggestedActions = []
      this.view = null
    },
    applySnapshot(snapshot: GameViewResponse): void {
      this.gameId = snapshot.game_id
      this.turn = snapshot.turn
      this.version = snapshot.version
      this.snapshotVersion = snapshot.snapshot_version
      this.status = snapshot.status
      this.lastText = snapshot.last_text ?? ''
      this.lastEffects = []
      this.lastSuggestedActions = []
    },
    /** 刷新 `GET /games/{id}` + `GET /view`：服务端权威版本链（不重置 busy/error）。 */
    async refreshAll(): Promise<void> {
      if (this.gameId === '') {
        return
      }
      const snapshot = await getGame(this.gameId)
      this.applySnapshot(snapshot)
      await this.refreshView()
    },
    /** 只拉状态面板数据源（D-M6-4：StatusPanel 唯一数据源）。 */
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
    },
    async createGame(options: CreateGameRequest): Promise<string> {
      this.busy = true
      this.error = ''
      this.errorCode = ''
      this.canRetry = false
      const actionKey = `create-game:${options.card_id ?? 'none'}:${options.seed ?? 'default'}:${options.scenario ?? 'm2'}`
      try {
        const created = await createGame(options, actionKey)
        clearIdempotencyKey(actionKey)
        this.applyCreated(created)
        await this.refreshView()
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
      this.error = ''
      this.errorCode = ''
      this.canRetry = false
      try {
        this.gameId = gameId
        const snapshot = await getGame(gameId)
        this.applySnapshot(snapshot)
        await this.refreshView()
      } catch (error) {
        this.captureError(error)
        throw error
      } finally {
        this.busy = false
      }
    },
    async submitTurn(input: string): Promise<void> {
      const text = input.trim()
      if (this.busy || this.gameId === '' || text === '') {
        return
      }
      this.busy = true
      this.error = ''
      this.errorCode = ''
      this.lastEffects = []
      this.lastSuggestedActions = []
      this.lastInput = text
      // 同一逻辑动作：手动重试复用 pendingKey；新动作由 version 派生新键。
      const actionKey = this.pendingKey ?? getOrCreate(`turn:${this.gameId}:${this.version}`)
      this.pendingKey = actionKey
      try {
        const result = await submitTurnApi(
          { game_id: this.gameId, input: text, expected_version: this.version },
          actionKey,
        )
        if (result.ok) {
          clearIdempotencyKey(actionKey)
          this.pendingKey = null
          this.canRetry = false
          this.lastText = result.text
          this.lastEffects = result.effects
          this.lastSuggestedActions = result.suggested_actions
          this.version = result.version
          await this.refreshView()
          return
        }
        // 业务失败：HTTP 200 + ok=false，零写入；同一输入重提视为新逻辑动作。
        clearIdempotencyKey(actionKey)
        this.pendingKey = null
        this.canRetry = false
        this.lastEffects = result.effects
        this.lastSuggestedActions = result.suggested_actions
        this.error = `${result.error_code ?? 'INVALID_INPUT'}：${result.text === '' ? '动作没有生效' : result.text}`
        this.errorCode = result.error_code ?? 'INVALID_INPUT'
      } catch (error) {
        if (error instanceof ApiClientError && error.isConflict) {
          clearIdempotencyKey(actionKey)
          this.pendingKey = null
          this.captureError(error)
          try {
            await this.refreshAll()
          } catch {
            // 刷新失败时保留原错误；用户可用「重试」再次触发（手动）
          }
          this.error = '版本冲突（CONFLICT）：已刷新到服务端最新版本，请重新提交。'
          this.errorCode = 'CONFLICT'
          this.canRetry = true
        } else {
          // 网络/未知失败：保留幂等键 → 用户手动重试时服务端可回放首次结果。
          this.captureError(error)
          this.canRetry = true
        }
      } finally {
        this.busy = false
      }
    },
    /** 用户点「重试」：手动重放上一次输入（不是自动重试）。 */
    async retry(): Promise<void> {
      if (this.lastInput === '') {
        return
      }
      await this.submitTurn(this.lastInput)
    },
  },
})
