/**
 * 人物卡库 store（M6-026）：列表分页/搜索、详情、服务端校验、multipart 导入。
 *
 * 口径（第 23 章 §23.5、第 6 章 §6.7）：
 * - 列表 `limit` 默认 20（服务端 1..100）、`offset` 分页；`query` 匹配 name；
 * - 校验以 `POST /character-cards/validate` 为准（客户端只做基础结构/max 64KB 预检）；
 * - 导入 multipart + 幂等键：手动重试复用 pendingImportKey；409 冲突后换新键；
 * - 全部只读/写接口都**无自动重试**，失败由用户手动触发。
 */

import { defineStore } from 'pinia'

import { ApiClientError, getCard, importCard, listCards, validateCard } from '@/api/client'
import type {
  CharacterCard,
  CharacterCardSummary,
  CharacterCardValidateResponse,
  CharacterCardWarning,
} from '@/api/types'
import { checkCardText, describeCardError, formatViolations } from '@/utils/cards'
import { clear as clearIdempotencyKey, getOrCreate } from '@/utils/idempotency'

export const CARD_LIST_DEFAULT_LIMIT = 20
export const CARD_LIST_MAX_LIMIT = 100

interface CardsState {
  items: CharacterCardSummary[]
  total: number
  query: string
  limit: number
  offset: number
  loading: boolean
  error: string
  errorCode: string
  detail: CharacterCard | null
  detailCardId: string
  detailLoading: boolean
  detailError: string
  detailErrorCode: string
  validateBusy: boolean
  validateResult: CharacterCardValidateResponse | null
  validateError: string
  importBusy: boolean
  importError: string
  importErrorCode: string
  importViolations: string[]
  importWarnings: CharacterCardWarning[]
  importCardId: string
  pendingImportKey: string | null
}

function initialState(): CardsState {
  return {
    items: [],
    total: 0,
    query: '',
    limit: CARD_LIST_DEFAULT_LIMIT,
    offset: 0,
    loading: false,
    error: '',
    errorCode: '',
    detail: null,
    detailCardId: '',
    detailLoading: false,
    detailError: '',
    detailErrorCode: '',
    validateBusy: false,
    validateResult: null,
    validateError: '',
    importBusy: false,
    importError: '',
    importErrorCode: '',
    importViolations: [],
    importWarnings: [],
    importCardId: '',
    pendingImportKey: null,
  }
}

export const useCardsStore = defineStore('cards', {
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
    captureListError(error: unknown): void {
      const info = describeCardError(error)
      this.error = info.message
      this.errorCode = info.code
    },
    /** 列表加载：`fromOffset` 缺省沿用当前 offset；`query` 缺省沿用当前查询。 */
    async loadList(options: { query?: string; offset?: number } = {}): Promise<void> {
      if (options.query !== undefined) {
        this.query = options.query
      }
      if (options.offset !== undefined) {
        this.offset = Math.max(0, Math.trunc(options.offset))
      }
      this.loading = true
      this.error = ''
      this.errorCode = ''
      try {
        const response = await listCards({
          query: this.query.trim() === '' ? undefined : this.query.trim(),
          limit: this.limit,
          offset: this.offset,
        })
        this.items = response.cards
        this.total = response.total
      } catch (error) {
        this.items = []
        this.total = 0
        this.captureListError(error)
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
    /** 详情加载（9 字段展示 + 开始游戏入口）；404/422 走统一错误文案。 */
    async loadDetail(cardId: string): Promise<void> {
      this.detail = null
      this.detailCardId = cardId
      this.detailLoading = true
      this.detailError = ''
      this.detailErrorCode = ''
      try {
        const response = await getCard(cardId)
        this.detail = response.card
      } catch (error) {
        const info = describeCardError(error)
        this.detailError = info.message
        this.detailErrorCode = info.code
        throw error
      } finally {
        this.detailLoading = false
      }
    },
    /** 服务端校验（只读；以服务端结果为准展示 valid/errors）。 */
    async validate(text: string): Promise<void> {
      this.validateResult = null
      this.validateError = ''
      const check = checkCardText(text)
      if (!check.ok) {
        this.validateError = check.error
        return
      }
      this.validateBusy = true
      try {
        const payload: unknown = JSON.parse(text)
        this.validateResult = await validateCard(payload)
      } catch (error) {
        this.validateError = describeCardError(error).message
      } finally {
        this.validateBusy = false
      }
    },
    /** 导入：客户端预检 → multipart + 幂等键 → 成功返回 card_id（由视图跳详情）。 */
    async importText(text: string): Promise<string | null> {
      this.importError = ''
      this.importErrorCode = ''
      this.importViolations = []
      this.importWarnings = []
      this.importCardId = ''
      const check = checkCardText(text)
      if (!check.ok) {
        this.importError = check.error
        this.importErrorCode = 'CLIENT'
        return null
      }
      const actionName = 'card-import'
      const actionKey = this.pendingImportKey ?? getOrCreate(actionName)
      this.pendingImportKey = actionKey
      this.importBusy = true
      try {
        const response = await importCard(text, actionKey, 'card.json')
        clearIdempotencyKey(actionName)
        this.pendingImportKey = null
        this.importCardId = response.card_id
        this.importWarnings = response.warnings
        return response.card_id
      } catch (error) {
        const info = describeCardError(error)
        this.importError = info.message
        this.importErrorCode = info.code
        if (error instanceof ApiClientError) {
          this.importViolations = formatViolations(error.violations)
          if (info.status === 409) {
            // 同键异参：清键，下一次提交自动换新键（不是自动重试）
            clearIdempotencyKey(actionName)
            this.pendingImportKey = null
          }
        }
        return null
      } finally {
        this.importBusy = false
      }
    },
    resetImport(): void {
      this.importError = ''
      this.importErrorCode = ''
      this.importViolations = []
      this.importWarnings = []
      this.importCardId = ''
    },
  },
})
