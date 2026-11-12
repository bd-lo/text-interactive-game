/**
 * N2-039 流式叙事：game store 的「尝试流式 → delta 显示 → done 权威 → 失败降级一次 → 取消」回归。
 *
 * 全部 fake fetch + 可控 ReadableStream（零 socket）；离线/LLM 未启用时行为必须与既有 `/turns` 完全一致。
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TurnStreamBusinessError } from '@/api/client'
import type { GamePlayerViewResponse, GameViewResponse, TurnResult } from '@/api/types'
import { useGameStore } from '@/stores/game'

const GAME_VIEW_2: GameViewResponse = {
  game_id: 'g1',
  turn: 2,
  version: 2,
  snapshot_version: 2,
  status: 'active',
  last_text: '你向北走进地窖。',
  scenario: 'story',
  ending_text: null,
  world_book: null,
  world_book_id: null,
  lore: null,
}
const PLAYER_VIEW_2: GamePlayerViewResponse = {
  game_id: 'g1',
  turn: 2,
  version: 2,
  snapshot_version: 2,
  status: 'active',
  scenario: 'story',
  hp: 20,
  max_hp: 20,
  inventory: [],
  room: { room_id: 'entry', title: '入口', description: '', exits: {}, items: [], npcs: [] },
  goal: null,
  ending_text: null,
  suggested_actions: [],
  world_book: null,
  lore: null,
}
const TURN_OK: TurnResult = {
  ok: true,
  turn_id: 2,
  version: 2,
  text: '权威完整文本。',
  events: [{ turn_id: 2, actor_id: 'a', method: 'move', params: {}, result: {} }],
  error_code: null,
  effects: [],
  suggested_actions: [],
  usage: {},
  narrative_source: 'llm',
}
const START = { type: 'start', turn_id: 2, expected_version: 1 } as const

const encoder = new TextEncoder()

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** 可控 NDJSON 流：测试可在任意时刻 push 事件 / close / error，用于验证「增量显示」。 */
function controlledStream() {
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null
  const stream = new ReadableStream<Uint8Array>({
    start(source) {
      controller = source
    },
  })
  return {
    response: new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8' },
    }),
    push(event: unknown): void {
      controller?.enqueue(encoder.encode(`${JSON.stringify(event)}\n`))
    },
    close(): void {
      controller?.close()
    },
    error(error: unknown): void {
      controller?.error(error)
    },
  }
}

function ndjsonResponse(...events: unknown[]): Response {
  return new Response(`${events.map((event) => JSON.stringify(event)).join('\n')}\n`, {
    status: 200,
    headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8' },
  })
}

/** 等待微任务/宏任务让流处理推进（fake fetch 已立即返回控制权）。 */
function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

interface UrlCalls {
  stream: number
  turn: number
}

function countCalls(mock: ReturnType<typeof mockFetch>): UrlCalls {
  const calls: UrlCalls = { stream: 0, turn: 0 }
  for (const call of mock.mock.calls) {
    if (String(call[0]) === '/api/v1/turns/stream') {
      calls.stream += 1
    } else if (String(call[0]) === '/api/v1/turns') {
      calls.turn += 1
    }
  }
  return calls
}

function seedGame(store: ReturnType<typeof useGameStore>, streamingAvailable = true): void {
  store.$patch({
    gameId: 'g1',
    turn: 1,
    version: 1,
    snapshotVersion: 1,
    status: 'active',
    scenario: 'story',
    lastText: '',
    history: [],
    view: {
      ...PLAYER_VIEW_2,
      turn: 1,
      version: 1,
      snapshot_version: 1,
    },
    streamingAvailable,
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  localStorage.clear()
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('game store 流式叙事（N2-039）', () => {
  it('LLM 已启用：走 /turns/stream，delta 增量显示、done.result.text 为权威并清空半截文本', async () => {
    const stream = controlledStream()
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns/stream') return stream.response
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    const pending = store.submitTurn('向北走')
    stream.push(START)
    stream.push({ type: 'delta', text: '未校验的' })
    stream.push({ type: 'delta', text: '半截文本' })
    await tick()
    expect(store.streaming).toBe(true)
    expect(store.streamText).toBe('未校验的半截文本')

    // done.result.text 与增量不同 → 以 done 为准（避免半截文本残留）
    stream.push({ type: 'done', result: { ...TURN_OK, text: '权威完整文本。' } })
    stream.close()
    await pending

    expect(countCalls(mock)).toEqual({ stream: 1, turn: 0 })
    expect(store.lastText).toBe('权威完整文本。')
    expect(store.streaming).toBe(false)
    expect(store.streamText).toBe('')
    expect(store.busy).toBe(false)
    expect(store.version).toBe(2)
    expect(store.history.map((entry) => entry.text)).toEqual(['权威完整文本。'])
    expect(store.retrySyncOnly).toBe(false)
  })

  it('replace 事件覆盖已显示增量（修复/模板回退路径）', async () => {
    const stream = controlledStream()
    mockFetch((url) => {
      if (url === '/api/v1/turns/stream') return stream.response
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    const pending = store.submitTurn('向北走')
    stream.push(START)
    stream.push({ type: 'delta', text: '太短' })
    stream.push({ type: 'replace', text: '模板回退的权威文本。', reason: 'TEMPLATE_FALLBACK' })
    await tick()
    expect(store.streamText).toBe('模板回退的权威文本。')

    stream.push({ type: 'done', result: { ...TURN_OK, text: '模板回退的权威文本。', narrative_source: 'template' } })
    stream.close()
    await pending

    expect(store.lastText).toBe('模板回退的权威文本。')
    expect(store.narrativeSource).toBe('template')
    expect(store.streamText).toBe('')
  })

  it('流式传输失败（404 开关关闭）→ 降级普通 /turns 恰好一次并成功', async () => {
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns/stream') {
        return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '流式端点未启用' })
      }
      if (url === '/api/v1/turns') return jsonResponse(200, TURN_OK)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    await store.submitTurn('向北走')

    expect(countCalls(mock)).toEqual({ stream: 1, turn: 1 })
    expect(store.lastText).toBe(TURN_OK.text)
    expect(store.error).toBe('')
    expect(store.retrySyncOnly).toBe(false)
    // 幂等键：流式与降级同步使用同一个键（服务端可回放首次结果）
    const streamKey = new Headers(mock.mock.calls[0]?.[1]?.headers).get('Idempotency-Key')
    const turnCall = mock.mock.calls.find((call) => String(call[0]) === '/api/v1/turns')
    expect(new Headers(turnCall?.[1]?.headers).get('Idempotency-Key')).toBe(streamKey)
  })

  it('流式无 done（协议中断）→ 仍降级同步一次，取权威结果', async () => {
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns/stream') {
        return ndjsonResponse(START, { type: 'delta', text: '半截' })
      }
      if (url === '/api/v1/turns') return jsonResponse(200, TURN_OK)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    await store.submitTurn('向北走')

    expect(countCalls(mock)).toEqual({ stream: 1, turn: 1 })
    expect(store.lastText).toBe(TURN_OK.text)
    expect(store.streamText).toBe('')
  })

  it('error 事件（业务失败，回合未提交）→ 不做同步降级；CONFLICT 走既有自愈', async () => {
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns/stream') {
        return ndjsonResponse(START, {
          type: 'error',
          error_code: 'CONFLICT',
          message: '版本已过期。',
        })
      }
      if (url === '/api/v1/games/g1') return jsonResponse(200, GAME_VIEW_2)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      if (url.startsWith('/api/v1/games/g1/replay')) {
        return jsonResponse(200, { events: [], from_seq: 1, to_seq: 0 })
      }
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    await store.submitTurn('向北走')

    expect(countCalls(mock).turn).toBe(0)
    expect(store.errorCode).toBe('CONFLICT')
    expect(store.canRetry).toBe(true)
    expect(store.busy).toBe(false)
    expect(store.streamText).toBe('')
    expect(store.history.some((entry) => entry.kind === 'notice')).toBe(true)
  })

  it('用户取消流式显示：busy 立即释放、无半截文本、可「重试（同步）」且不重复提交', async () => {
    const stream = controlledStream()
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns/stream') return stream.response
      if (url === '/api/v1/turns') return jsonResponse(200, TURN_OK)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    const pending = store.submitTurn('向北走')
    stream.push(START)
    stream.push({ type: 'delta', text: '已显示一半' })
    await tick()
    expect(store.streaming).toBe(true)
    expect(store.streamText).toBe('已显示一半')

    store.cancelStream()
    stream.error(new DOMException('aborted', 'AbortError'))
    await pending

    expect(store.streaming).toBe(false)
    expect(store.streamText).toBe('')
    expect(store.busy).toBe(false)
    expect(store.canRetry).toBe(true)
    expect(store.retrySyncOnly).toBe(true)
    expect(store.streamNotice).toContain('已取消')
    expect(countCalls(mock).turn).toBe(0) // 取消不自动降级/重试

    // 手动「重试（同步）」：显式走 /turns，不再尝试流式；复用幂等键按服务端回放确认结果
    await store.retry()
    expect(countCalls(mock).stream).toBe(1)
    expect(countCalls(mock).turn).toBe(1)
    expect(store.lastText).toBe(TURN_OK.text)
    expect(store.retrySyncOnly).toBe(false)
  })

  it('降级同步也失败：保留手动重试为「重试（同步）」，且不自动重试', async () => {
    let turnCalls = 0
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns/stream') {
        return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '未启用' })
      }
      if (url === '/api/v1/turns') {
        turnCalls += 1
        if (turnCalls === 1) {
          throw new TypeError('fetch failed')
        }
        return jsonResponse(200, TURN_OK)
      }
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store)

    await store.submitTurn('向北走')

    expect(countCalls(mock).turn).toBe(1) // 只降级一次，无自动重试
    expect(store.canRetry).toBe(true)
    expect(store.retrySyncOnly).toBe(true)
    expect(store.error).not.toBe('')

    await store.retry()
    expect(countCalls(mock).stream).toBe(1) // 手动重试走同步，不再流式
    expect(countCalls(mock).turn).toBe(2)
    expect(store.lastText).toBe(TURN_OK.text)
  })

  it('LLM 未启用/离线：完全不请求 /turns/stream（既有路径零变化）', async () => {
    const mock = mockFetch((url) => {
      if (url === '/api/v1/turns') return jsonResponse(200, TURN_OK)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    seedGame(store, false)

    await store.submitTurn('向北走')

    expect(countCalls(mock)).toEqual({ stream: 0, turn: 1 })
    expect(store.lastText).toBe(TURN_OK.text)
  })

  it('refreshStreamingAvailability：仅 enabled=true 才开启流式（读取失败保守关闭）', async () => {
    const statusBase = {
      configured: true,
      enabled: true,
      source: 'user',
      provider: 'deepseek',
      model: 'deepseek-flash',
      api_base: 'https://api.deepseek.com',
      masked_key: 'sk-***',
      has_saved_user_key: true,
      detail: '',
    }
    mockFetch((url) => {
      if (url === '/api/v1/settings/llm') return jsonResponse(200, statusBase)
      throw new Error(`unexpected ${url}`)
    })
    const store = useGameStore()
    await store.refreshStreamingAvailability()
    expect(store.streamingAvailable).toBe(true)

    mockFetch(() => jsonResponse(200, { ...statusBase, enabled: false }))
    await store.refreshStreamingAvailability()
    expect(store.streamingAvailable).toBe(false)

    mockFetch(() => {
      throw new TypeError('fetch failed')
    })
    await store.refreshStreamingAvailability()
    expect(store.streamingAvailable).toBe(false)
  })

  it('TurnStreamBusinessError 与普通 ApiClientError 区分（供降级判定）', () => {
    const error = new TurnStreamBusinessError('INVALID_INPUT', '没听懂')
    expect(error.status).toBe(200)
    expect(error.code).toBe('INVALID_INPUT')
    expect(error.isConflict).toBe(false)
  })
})
