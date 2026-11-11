/**
 * 世界书 store（H4）：列表/搜索分页、详情、服务端校验、multipart 导入、软删、开局绑定。
 *
 * 口径（H4-CONTRACT §2/§7）：
 * - 列表 `limit` 默认 20（服务端 1..100）、`offset` 分页、`query` 匹配 name；
 * - 校验以 `POST /world-books/validate` 为准（客户端只做基础结构/256KB 预检）；
 * - 导入 multipart + 幂等键：手动重试复用 pendingImportKey；409 冲突后换新键；
 * - 删除为软删（幂等），成功后刷新列表；全部接口无自动重试（失败由用户手动触发）。
 */

import { defineStore } from 'pinia'

import {
  ApiClientError,
  deleteWorldBook,
  getWorldBook,
  importWorldBook,
  listWorldBooks,
  validateWorldBook,
} from '@/api/client'
import type {
  WorldBookDetailBody,
  WorldBookEntry,
  WorldBookImportResponse,
  WorldBookSummary,
  WorldBookValidateResponse,
  WorldBookWarning,
} from '@/api/types'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'
import { clear as clearIdempotencyKey, getOrCreate } from '@/utils/idempotency'
import {
  checkWorldBookText,
  describeWorldBookError,
} from '@/utils/worldBooks'

export const WORLD_BOOK_LIST_DEFAULT_LIMIT = 20
export const WORLD_BOOK_LIST_MAX_LIMIT = 100

interface WorldBooksState {
  items: WorldBookSummary[]
  total: number
  query: string
  limit: number
  offset: number
  loading: boolean
  error: PlayerErrorInfo | null
  detail: WorldBookDetailBody | null
  entries: WorldBookEntry[]
  detailId: string
  detailLoading: boolean
  detailError: PlayerErrorInfo | null
  detailLoaded: boolean
  validateBusy: boolean
  validateResult: WorldBookValidateResponse | null
  validateError: string
  importBusy: boolean
  importError: PlayerErrorInfo | null
  importWarnings: WorldBookWarning[]
  importResult: WorldBookImportResponse | null
  pendingImportKey: string | null
  deleteBusy: boolean
  deleteError: PlayerErrorInfo | null
  deleteArmed: boolean
}

function initialState(): WorldBooksState {
  return {
    items: [],
    total: 0,
    query: '',
    limit: WORLD_BOOK_LIST_DEFAULT_LIMIT,
    offset: 0,
    loading: false,
    error: null,
    detail: null,
    entries: [],
    detailId: '',
    detailLoading: false,
    detailError: null,
    detailLoaded: false,
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

export const useWorldBooksStore = defineStore('worldBooks', {
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
  },
  actions: {
    /** 列表加载（`fromOffset` 缺省沿用当前 offset；`query` 缺省沿用当前查询）。 */
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
        const response = await listWorldBooks({
          query: this.query.trim() === '' ? undefined : this.query.trim(),
          limit: this.limit,
          offset: this.offset,
        })
        this.items = response.world_books
        this.total = response.total
      } catch (error) {
        this.items = []
        this.total = 0
        this.error = describeError(error)
        throw error
      } finally {
        this.loading = false
      }
    },
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
    /** 详情加载（摘要 + 条目；未知/已删 404）。 */
    async loadDetail(worldBookId: string): Promise<void> {
      this.detailId = worldBookId
      this.detail = null
      this.entries = []
      this.detailLoading = true
      this.detailError = null
      try {
        const response = await getWorldBook(worldBookId)
        this.detail = response.world_book
        this.entries = response.entries
        this.detailLoaded = true
      } catch (error) {
        this.detailLoaded = false
        this.detailError = describeWorldBookError(error)
        throw error
      } finally {
        this.detailLoading = false
      }
    },
    /** 服务端校验（只读；结果以服务端 valid/errors/warnings/preview 为准）。 */
    async validate(text: string): Promise<void> {
      this.validateResult = null
      this.validateError = ''
      const check = checkWorldBookText(text)
      if (!check.ok) {
        this.validateError = check.error
        return
      }
      this.validateBusy = true
      try {
        const payload: unknown = JSON.parse(text)
        this.validateResult = await validateWorldBook(payload)
      } catch (error) {
        this.validateError = describeWorldBookError(error).message
      } finally {
        this.validateBusy = false
      }
    },
    /** 导入：客户端预检 → multipart + 幂等键 → 成功后记录结果（由视图跳详情）。 */
    async importText(text: string, options: { name?: string; filename?: string } = {}): Promise<string | null> {
      this.importError = null
      this.importWarnings = []
      this.importResult = null
      const check = checkWorldBookText(text)
      if (!check.ok) {
        this.importError = { title: '内容不对', message: check.error, detail: check.error }
        return null
      }
      const actionName = 'world-book-import'
      const actionKey = this.pendingImportKey ?? getOrCreate(actionName)
      this.pendingImportKey = actionKey
      this.importBusy = true
      try {
        const response = await importWorldBook(
          text,
          actionKey,
          { filename: options.filename, name: options.name },
        )
        clearIdempotencyKey(actionName)
        this.pendingImportKey = null
        this.importResult = response
        this.importWarnings = response.warnings ?? []
        return response.world_book_id
      } catch (error) {
        this.importError = describeWorldBookError(error)
        if (error instanceof ApiClientError && (error.status === 409 || error.code === 'CONFLICT')) {
          // 同键异参：清键，下一次提交自动换新键（不是自动重试）
          clearIdempotencyKey(actionName)
          this.pendingImportKey = null
        }
        return null
      } finally {
        this.importBusy = false
      }
    },
    /** 软删除（二次确认由视图控制）；成功后从列表移除并刷新。 */
    async remove(worldBookId: string): Promise<boolean> {
      this.deleteBusy = true
      this.deleteError = null
      const actionKey = `world-book-delete:${worldBookId}`
      try {
        await deleteWorldBook(worldBookId, actionKey)
        clearIdempotencyKey(actionKey)
        this.items = this.items.filter((item) => item.world_book_id !== worldBookId)
        this.total = Math.max(0, this.total - 1)
        this.deleteArmed = false
        return true
      } catch (error) {
        this.deleteError = describeWorldBookError(error)
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
    },
  },
})
