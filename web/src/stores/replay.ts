/**
 * 回放 store（M6-027）：分页加载 `GET /api/v1/games/{id}/replay` + 时间轴定位。
 *
 * 口径（D-M6-3 / 第 6 章 §6.7）：
 * - 首屏 `from_seq=1`、`limit` 默认 200（上限 500）；事件多时按 `from_seq=已加载末 seq + 1` 分批；
 * - 只读：只 GET，不写状态、不产生事件；无自动重试（失败后由用户点「重试加载」）；
 * - 索引只在**已加载事件**内定位，越界 clamp；不据此推演权威对局状态。
 */

import { defineStore } from 'pinia'

import { ApiClientError, getReplay } from '@/api/client'
import type { ReplayEvent } from '@/api/types'
import { clampIndex, indexOfSeq, lastSeqOf, stepIndex } from '@/utils/replay'

export const REPLAY_DEFAULT_PAGE_SIZE = 200
export const REPLAY_MAX_LIMIT = 500

/** `limit` 归一化：非法值回默认、越界 clamp 到 [1, 500]。 */
export function normalizeReplayLimit(value: number): number {
  if (!Number.isFinite(value)) {
    return REPLAY_DEFAULT_PAGE_SIZE
  }
  return Math.min(REPLAY_MAX_LIMIT, Math.max(1, Math.trunc(value)))
}

interface ReplayState {
  gameId: string
  events: ReplayEvent[]
  fromSeq: number
  toSeq: number
  hasMore: boolean
  loading: boolean
  error: string
  errorCode: string
  selectedIndex: number
}

function initialState(): ReplayState {
  return {
    gameId: '',
    events: [],
    fromSeq: 1,
    toSeq: 0,
    hasMore: false,
    loading: false,
    error: '',
    errorCode: '',
    selectedIndex: -1,
  }
}

export const useReplayStore = defineStore('replay', {
  state: initialState,
  getters: {
    selectedEvent(state): ReplayEvent | null {
      return state.events[state.selectedIndex] ?? null
    },
    loadedCount(state): number {
      return state.events.length
    },
    lastSeq(state): number {
      return lastSeqOf(state.events)
    },
    selectedSeq(state): number {
      return state.events[state.selectedIndex]?.seq ?? 0
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
    /** 首屏加载：重置后从 from_seq=1 拉第一页，默认选中末条（最近事件）。 */
    async loadFirstPage(gameId: string, options: { limit?: number } = {}): Promise<void> {
      this.gameId = gameId
      this.events = []
      this.fromSeq = 1
      this.toSeq = 0
      this.hasMore = false
      this.loading = true
      this.error = ''
      this.errorCode = ''
      this.selectedIndex = -1
      const limit = normalizeReplayLimit(options.limit ?? REPLAY_DEFAULT_PAGE_SIZE)
      try {
        const page = await getReplay(gameId, { fromSeq: 1, limit })
        this.applyPage(page.events, page.from_seq, page.to_seq, limit, true)
        this.selectedIndex = this.events.length > 0 ? this.events.length - 1 : -1
      } catch (error) {
        this.captureError(error)
        throw error
      } finally {
        this.loading = false
      }
    },
    /** 追加下一页：from_seq = 已加载末 seq + 1；空窗口/不足一页则 hasMore=false。 */
    async loadMore(): Promise<void> {
      if (this.loading || this.gameId === '' || !this.hasMore) {
        return
      }
      const limit = REPLAY_DEFAULT_PAGE_SIZE
      this.loading = true
      this.error = ''
      this.errorCode = ''
      try {
        const fromSeq = this.lastSeq + 1
        const page = await getReplay(this.gameId, { fromSeq, limit })
        this.applyPage(page.events, page.from_seq, page.to_seq, limit, false)
      } catch (error) {
        this.captureError(error)
        throw error
      } finally {
        this.loading = false
      }
    },
    /** 合并一页（按 `seq` 去重升序）；`hasMore` 以「本页是否满页」判定。 */
    applyPage(
      incoming: readonly ReplayEvent[],
      fromSeq: number,
      toSeq: number,
      limit: number,
      replace: boolean,
    ): void {
      const known = new Set(this.events.map((event) => event.seq))
      const fresh = incoming.filter((event) => !known.has(event.seq))
      this.events = replace ? [...fresh] : [...this.events, ...fresh]
      this.events.sort((left, right) => left.seq - right.seq)
      this.fromSeq = fromSeq
      this.toSeq = toSeq
      this.hasMore = incoming.length >= limit && fresh.length > 0
      this.selectedIndex = clampIndex(this.selectedIndex, this.events.length)
    },
    /** 时间轴/拖动定位（任意值 clamp 到已加载范围）。 */
    select(index: number): void {
      this.selectedIndex = clampIndex(index, this.events.length)
    },
    /** 相对定位：首=大负数、末=大正数，或 ±1 步进。 */
    step(delta: number): void {
      this.selectedIndex = stepIndex(this.selectedIndex, this.events.length, delta)
    },
    /** 按全局 `seq` 定位（内容区点击/深链使用）。 */
    selectSeq(seq: number): void {
      const index = indexOfSeq(this.events, seq)
      if (index >= 0) {
        this.selectedIndex = index
      }
    },
  },
})
