import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClientError, getReplay } from '@/api/client'
import { useReplayStore } from '@/stores/replay'
import {
  clampIndex,
  indexOfSeq,
  lastSeqOf,
  NARRATIVE_FALLBACK,
  narrativeOf,
  stepIndex,
} from '@/utils/replay'
import type { ReplayEvent } from '@/api/types'

function makeEvent(seq: number, turnId: number, narrative: string | null = `第 ${seq} 条叙事。`): ReplayEvent {
  return {
    id: `evt_${String(seq).padStart(6, '0')}`,
    seq,
    turn_id: turnId,
    actor_id: 'player',
    method: 'look',
    event_type: 'ROOM_LOOKED',
    params: { api_key: 'should-not-render' },
    result: narrative === null ? {} : { narrative },
    rng_seed: 42,
    ts: '2026-11-10T13:00:00Z',
  }
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init))
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('replay client（D-M6-3：只读、分页参数、运行时字段校验）', () => {
  it('携带 from_seq/limit、不带幂等键，并解析 events/from_seq/to_seq', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, { events: [makeEvent(2, 2)], from_seq: 2, to_seq: 2 }),
    )

    const page = await getReplay('g1', { fromSeq: 2, limit: 100 })

    expect(page.from_seq).toBe(2)
    expect(page.to_seq).toBe(2)
    expect(page.events[0]?.seq).toBe(2)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/games/g1/replay?from_seq=2&limit=100')
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toBeNull() // 只读端点不带幂等键
  })

  it('事件字段漂移 → ApiClientError（不裸 as T）', async () => {
    const broken = makeEvent(1, 1) as unknown as Record<string, unknown>
    delete broken.rng_seed
    mockFetch(() => jsonResponse(200, { events: [broken], from_seq: 1, to_seq: 1 }))

    const error = (await getReplay('g1').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('INTERNAL')
    expect(error.message).toContain('契约')
  })

  it('未知存档 404 失败信封 → ApiClientError NOT_FOUND（不自动重试）', async () => {
    let calls = 0
    mockFetch(() => {
      calls += 1
      return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '存档不存在: nope' })
    })

    const error = (await getReplay('nope').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('NOT_FOUND')
    expect(calls).toBe(1)
  })
})

describe('回放定位纯函数（时间轴 clamp/step）', () => {
  it('clampIndex/stepIndex：越界、NaN、空列表均安全', () => {
    expect(clampIndex(-5, 3)).toBe(0)
    expect(clampIndex(9, 3)).toBe(2)
    expect(clampIndex(Number.NaN, 3)).toBe(0)
    expect(clampIndex(1.9, 3)).toBe(1)
    expect(clampIndex(0, 0)).toBe(-1)

    expect(stepIndex(0, 3, -1)).toBe(0)
    expect(stepIndex(1, 3, 1)).toBe(2)
    expect(stepIndex(2, 3, 1)).toBe(2)
    expect(stepIndex(0, 0, 1)).toBe(-1)
  })

  it('indexOfSeq/lastSeqOf/narrativeOf：seq 定位与叙事兜底', () => {
    const events = [makeEvent(1, 1), makeEvent(2, 2), makeEvent(3, 2, null)]

    expect(indexOfSeq(events, 2)).toBe(1)
    expect(indexOfSeq(events, 99)).toBe(-1)
    expect(lastSeqOf(events)).toBe(3)
    expect(lastSeqOf([])).toBe(0)
    expect(narrativeOf(events[0] ?? null)).toBe('第 1 条叙事。')
    expect(narrativeOf(events[2] ?? null)).toBe(NARRATIVE_FALLBACK)
    expect(narrativeOf(null)).toBe(NARRATIVE_FALLBACK)
  })
})

describe('replay store 分页（from_seq 续页 + seq 去重升序）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('首屏默认选中末条；满页 hasMore，追加下一页后合并去重', async () => {
    const calls: string[] = []
    mockFetch((url) => {
      calls.push(url)
      if (url.includes('from_seq=1')) {
        return jsonResponse(200, { events: [makeEvent(1, 1), makeEvent(2, 1)], from_seq: 1, to_seq: 2 })
      }
      if (url.includes('from_seq=3')) {
        return jsonResponse(200, { events: [makeEvent(3, 2)], from_seq: 3, to_seq: 3 })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })
    const store = useReplayStore()

    await store.loadFirstPage('g1', { limit: 2 })

    expect(store.loadedCount).toBe(2)
    expect(store.selectedSeq).toBe(2) // 默认定位最近事件
    expect(store.hasMore).toBe(true)

    await store.loadMore()

    expect(store.loadedCount).toBe(3)
    expect(store.lastSeq).toBe(3)
    expect(store.hasMore).toBe(false)
    expect(calls[1]).toContain('from_seq=3')
  })

  it('加载失败：错误进 store（NO 自动重试），时间轴保持空', async () => {
    let calls = 0
    mockFetch(() => {
      calls += 1
      return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '存档不存在: nope' })
    })
    const store = useReplayStore()

    await store.loadFirstPage('nope').catch(() => undefined)

    expect(calls).toBe(1)
    expect(store.loadedCount).toBe(0)
    expect(store.errorCode).toBe('NOT_FOUND')
    expect(store.error).not.toBe('')
  })
})
