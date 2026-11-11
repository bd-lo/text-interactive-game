/**
 * 用户自上传场景 store（scenario modules v1）：列表分页/搜索、详情、服务端校验、multipart 导入、软删。
 *
 * 口径（`docs/design/user-scenario-modules.md` §2.3/§3.4）：
 * - 列表 `limit` 默认 20（服务端 1..100）、`offset` 分页、`q` 搜索 title/author/slug；
 * - 校验以 `POST /scenarios/validate` 为准（客户端只做基础结构/256KB/UTF-8 预检）；
 * - 导入 multipart + 幂等键：手动重试复用 pendingImportKey；409 冲突后换新键（不是自动重试）；
 * - 删除为软删（服务端 204；不影响已开局存档），成功后刷新本地列表；全部接口无自动重试。
 */

import { defineStore } from 'pinia'

import {
  ApiClientError,
  deleteScenario,
  getScenario,
  importScenario,
  listScenarios,
  validateScenario,
} from '@/api/client'
import type {
  ScenarioDetailResponse,
  ScenarioImportResponse,
  ScenarioSummary,
  ScenarioValidateResponse,
  ScenarioWarning,
} from '@/api/types'
import type { PlayerErrorInfo } from '@/utils/errors'
import { clear as clearIdempotencyKey, getOrCreate } from '@/utils/idempotency'
import { checkScenarioText, describeScenarioError } from '@/utils/scenarios'

export const SCENARIO_LIST_DEFAULT_LIMIT = 20
export const SCENARIO_LIST_MAX_LIMIT = 100

interface ScenariosState {
  items: ScenarioSummary[]
  total: number
  query: string
  limit: number
  offset: number
  loading: boolean
  error: PlayerErrorInfo | null
  detail: ScenarioDetailResponse | null
  detailId: string
  detailLoading: boolean
  detailError: PlayerErrorInfo | null
  validateBusy: boolean
  validateResult: ScenarioValidateResponse | null
  validateError: string
  importBusy: boolean
  importError: PlayerErrorInfo | null
  importWarnings: ScenarioWarning[]
  importResult: ScenarioImportResponse | null
  pendingImportKey: string | null
  deleteBusy: boolean
  deleteError: PlayerErrorInfo | null
  deleteArmed: boolean
}

function initialState(): ScenariosState {
  return {
    items: [],
    total: 0,
    query: '',
    limit: SCENARIO_LIST_DEFAULT_LIMIT,
    offset: 0,
    loading: false,
    error: null,
    detail: null,
    detailId: '',
    detailLoading: false,
    detailError: null,
    validateBusy: false,
    validateResult: null,
    validateError: '',
    importBusy: false,
    importError: null,
    importWarnings: [],
    importResult: null,
    pendingImportKey: null,
    deleteBusy: false,
    deleteError: null,
    deleteArmed: false,
  }
}

export const useScenariosStore = defineStore('scenarios', {
  state: initialState,
  getters: {
    page(state): number {
      return state.limit > 0 ? Math.floor(state.offset / state.limit) + 1 : 1
    },
    totalPages(state): number {
      return state.total > 0 ? Math.ceil(state.total / state.limit) : 1
    },
    canPrev(state): boolean {
      return state.offset > 0 && !state.loading
    },
    canNext(state): boolean {
      return state.offset + state.limit < state.total && !state.loading
    },
    /** 校验是否允许导入：必须服务端 validate 明确 `ok=true`（导入前先预检，§3.4）。 */
    canImport(state): boolean {
      return (
        !state.importBusy &&
        !state.validateBusy &&
        state.validateResult !== null &&
        state.validateResult.ok
      )
    },
  },
  actions: {
    /** 列表加载（`offset`/`query` 缺省沿用当前值；失败清空列表并抛给视图决定是否重试）。 */
    async loadList(options: { query?: string; offset?: number } = {}): Promise<void> {
      if (options.query !== undefined) {
        this.query = options.query
      }
      if (options.offset !== undefined) {
        this.offset = Math.max(0, Math.trunc(options.offset))
      }
      this.loading = true
      this.error = null
      try {
        const response = await listScenarios({
          q: this.query.trim() === '' ? undefined : this.query.trim(),
          limit: this.limit,
          offset: this.offset,
        })
        this.items = response.scenarios
        this.total = response.total
      } catch (error) {
        this.items = []
        this.total = 0
        this.error = describeScenarioError(error)
        throw error
      } finally {
        this.loading = false
      }
    },
    /** 搜索（手动触发，无自动请求）：重置到第一页。 */
    async search(query: string): Promise<void> {
      await this.loadList({ query, offset: 0 })
    },
    async prevPage(): Promise<void> {
      if (!this.canPrev) {
        return
      }
      await this.loadList({ offset: Math.max(0, this.offset - this.limit) })
    },
    async nextPage(): Promise<void> {
      if (!this.canNext) {
        return
      }
      await this.loadList({ offset: this.offset + this.limit })
    },
    /** 详情加载（原始定义 + summary；未知/已删 404）。 */
    async loadDetail(scenarioId: string): Promise<void> {
      this.detailId = scenarioId
      this.detail = null
      this.detailLoading = true
      this.detailError = null
      try {
        this.detail = await getScenario(scenarioId)
      } catch (error) {
        this.detailError = describeScenarioError(error)
        throw error
      } finally {
        this.detailLoading = false
      }
    },
    /** 服务端只读校验：成功记 `{ok,errors,warnings,summary}`；本地预检失败不入请求。 */
    async validate(text: string): Promise<void> {
      this.validateResult = null
      this.validateError = ''
      const check = checkScenarioText(text)
      if (!check.ok) {
        this.validateError = check.error
        return
      }
      this.validateBusy = true
      try {
        const scenario: unknown = JSON.parse(text)
        this.validateResult = await validateScenario(scenario)
      } catch (error) {
        this.validateError = describeScenarioError(error).message
      } finally {
        this.validateBusy = false
      }
    },
    /** 导入：客户端预检 → multipart + 幂等键 → 成功返回 scenario_id（由视图跳详情）。 */
    async importText(
      text: string,
      options: { filename?: string } = {},
    ): Promise<string | null> {
      this.importError = null
      this.importWarnings = []
      this.importResult = null
      const check = checkScenarioText(text)
      if (!check.ok) {
        this.importError = { title: '内容不对', message: check.error, detail: check.error }
        return null
      }
      const actionKey = this.pendingImportKey ?? getOrCreate('scenario-import')
      this.pendingImportKey = actionKey
      this.importBusy = true
      try {
        const response = await importScenario(text, actionKey, options.filename)
        clearIdempotencyKey(actionKey)
        this.pendingImportKey = null
        this.importResult = response
        this.importWarnings = response.warnings
        return response.scenario_id
      } catch (error) {
        this.importError = describeScenarioError(error)
        if (error instanceof ApiClientError && (error.status === 409 || error.code === 'CONFLICT')) {
          // 同键异参：清键，下一次提交自动换新键（不是自动重试）
          clearIdempotencyKey(actionKey)
          this.pendingImportKey = null
        }
        return null
      } finally {
        this.importBusy = false
      }
    },
    /**
     * 软删除（服务端 204；二次确认由视图控制）：成功从本地列表移除，
     * 若当前详情正是该场景则清空；失败保留错误供视图展示。
     */
    async remove(scenarioId: string): Promise<boolean> {
      this.deleteBusy = true
      this.deleteError = null
      const actionKey = `scenario-delete:${scenarioId}`
      try {
        await deleteScenario(scenarioId, actionKey)
        clearIdempotencyKey(actionKey)
        this.items = this.items.filter((item) => item.scenario_id !== scenarioId)
        this.total = Math.max(0, this.total - 1)
        if (this.detail?.scenario_id === scenarioId) {
          this.detail = null
          this.detailId = ''
        }
        this.deleteArmed = false
        return true
      } catch (error) {
        this.deleteError = describeScenarioError(error)
        return false
      } finally {
        this.deleteBusy = false
      }
    },
    resetImport(): void {
      this.importError = null
      this.importWarnings = []
      this.importResult = null
    },
    resetValidate(): void {
      this.validateResult = null
      this.validateError = ''
    },
    armDelete(): void {
      this.deleteArmed = true
    },
    disarmDelete(): void {
      this.deleteArmed = false
      this.deleteError = null
    },
  },
})
