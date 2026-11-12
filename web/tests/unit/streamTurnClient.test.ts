/**
 * N2-039 流式叙事：`streamTurn` NDJSON 客户端（fetch + ReadableStream）回归。
 *
 * 覆盖协议 5 事件、分帧/UTF-8 跨 chunk、idempotency/AbortSignal、业务 error 与传输失败区分、
 * 超时/取消、非 NDJSON/字段漂移契约错误；全程 fake fetch + fake ReadableStream（零 socket）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  TURN_STREAM_TIMEOUT_MS,
  TurnStreamBusinessError,
  streamTurn,
} from '@/api/client'
import type { TurnRequest, TurnResult, TurnStreamEvent } from '@/api/types'

const BODY: TurnRequest = { game_id: 'g1', input: '向北走', expected_version: 1 }

const TURN_RESULT: TurnResult = {
  ok: true,
  turn_id: 2,
  version: 2,
  text: '你向北走进地窖。',
  events: [],
  error_code: null,
  effects: [],
  suggested_actions: [],
  usage: {},
  narrative_source: 'llm',
}

const START = { type: 'start', turn_id: 2, expected_version: 1 } as const
const DONE = { type: 'done', result: TURN_RESULT } as const

const encoder = new TextEncoder()

function ndjson(...events: unknown[]): string {
  return `${events.map((event) => JSON.stringify(event)).join('\n')}\n`
}

function streamFromChunks(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk)
      }
      controller.close()
    },
  })
}

function streamResponse(chunks: Uint8Array[], headers: Record<string, string> = {}): Response {
  return new Response(streamFromChunks(chunks), {
    status: 200,
    headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8', ...headers },
  })
}

function textStreamResponse(text: string, headers?: Record<string, string>): Response {
  return streamResponse([encoder.encode(text)], headers)
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('streamTurn（NDJSON 客户端）', () => {
  it('按 line 解析 5 类事件：返回 done.result 权威结果并逐事件回调', async () => {
    const mock = mockFetch(() =>
      textStreamResponse(
        ndjson(
          START,
          { type: 'delta', text: '你向北' },
          { type: 'delta', text: '走进地窖。' },
          DONE,
        ),
      ),
    )
    const events: TurnStreamEvent[] = []

    const result = await streamTurn(BODY, 'turn:g1:2', { onEvent: (event) => events.push(event) })

    expect(result).toEqual(TURN_RESULT)
    expect(events.map((event) => event.type)).toEqual(['start', 'delta', 'delta', 'done'])
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/turns/stream')
    expect(call[1]?.method).toBe('POST')
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toBe('turn:g1:2')
    expect(headers.get('Accept')).toBe('application/x-ndjson')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(call[1]?.body))).toEqual(BODY)
  })

  it('分帧健壮：事件跨 chunk、UTF-8 多字节字符被字节切断也能还原', async () => {
    const text = ndjson(START, { type: 'delta', text: '你沿着北边的通道动身' }, DONE)
    const bytes = encoder.encode(text)
    // 在任意 3 个位置切断（含中文字符中间）——TextDecoder(stream:true) 必须正确拼接。
    const cuts = [1, 5, 7, 13, 29, 31, bytes.length]
    const chunks: Uint8Array[] = []
    let previous = 0
    for (const cut of cuts) {
      chunks.push(bytes.slice(previous, cut))
      previous = cut
    }
    mockFetch(() => streamResponse(chunks.filter((chunk) => chunk.length > 0)))
    const deltas: string[] = []

    const result = await streamTurn(BODY, 'turn:g1:2', {
      onEvent: (event) => {
        if (event.type === 'delta') {
          deltas.push(event.text)
        }
      },
    })

    expect(deltas.join('')).toBe('你沿着北边的通道动身')
    expect(result.text).toBe('你向北走进地窖。')
  })

  it('replace 事件回调文本（最终仍以 done.result.text 为准）', async () => {
    const revised = { ...TURN_RESULT, text: '修复后的权威全文。' }
    mockFetch(() =>
      textStreamResponse(
        ndjson(
          START,
          { type: 'delta', text: '太短' },
          { type: 'replace', text: '修复后的权威全文。', reason: 'NARRATIVE_REVISED' },
          { type: 'done', result: revised },
        ),
      ),
    )
    const events: TurnStreamEvent[] = []

    const result = await streamTurn(BODY, 'turn:g1:2', { onEvent: (event) => events.push(event) })

    const replace = events.find((event) => event.type === 'replace')
    expect(replace).toEqual({
      type: 'replace',
      text: '修复后的权威全文。',
      reason: 'NARRATIVE_REVISED',
    })
    expect(result.text).toBe('修复后的权威全文。')
  })

  it('未知 type 向前兼容：忽略不崩、不回调', async () => {
    const events: TurnStreamEvent[] = []
    mockFetch(() =>
      textStreamResponse(
        ndjson(START, { type: 'heartbeat', value: 1 }, { type: 'delta', text: '字' }, DONE),
      ),
    )

    const result = await streamTurn(BODY, 'turn:g1:2', { onEvent: (event) => events.push(event) })

    expect(events.map((event) => event.type)).toEqual(['start', 'delta', 'done'])
    expect(result.ok).toBe(true)
  })

  it('error 事件 → TurnStreamBusinessError（回合未提交；非传输错误）', async () => {
    mockFetch(() =>
      textStreamResponse(
        ndjson(START, {
          type: 'error',
          error_code: 'CONFLICT',
          message: '你的对局版本已经过期，请刷新对局视图后重试。',
        }),
      ),
    )

    const error = (await streamTurn(BODY, 'turn:g1:2').catch((reason: unknown) => reason)) as Error

    expect(error).toBeInstanceOf(TurnStreamBusinessError)
    expect((error as TurnStreamBusinessError).code).toBe('CONFLICT')
    expect((error as TurnStreamBusinessError).status).toBe(200)
    expect(error.message).toContain('版本已经过期')
  })

  it('非 2xx（如开关关闭 404）→ ApiClientError 携带错误信封（供 store 降级同步）', async () => {
    mockFetch(
      () =>
        new Response(JSON.stringify({ ok: false, error_code: 'NOT_FOUND', message: '流式端点未启用' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
    )

    const error = (await streamTurn(BODY, 'turn:g1:2').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(404)
    expect(error.code).toBe('NOT_FOUND')
  })

  it('非 NDJSON 响应（如 SPA HTML 兜底）→ 契约错误（不裸 parse）', async () => {
    mockFetch(() => new Response('<html>spa fallback</html>', { status: 200, headers: { 'Content-Type': 'text/html' } }))

    const error = (await streamTurn(BODY, 'turn:g1:2').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.message).toContain('NDJSON')
  })

  it('事件字段漂移（delta 缺 text）→ 契约错误（不做裸 as T）', async () => {
    mockFetch(() => textStreamResponse(ndjson(START, { type: 'delta' })))

    const error = (await streamTurn(BODY, 'turn:g1:2').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.message).toContain('契约')
  })

  it('流提前结束且无 done → STREAM_INCOMPLETE（提示刷新/手动重试）', async () => {
    mockFetch(() => textStreamResponse(ndjson(START, { type: 'delta', text: '半截' })))

    const error = (await streamTurn(BODY, 'turn:g1:2').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('STREAM_INCOMPLETE')
  })

  it('外部 AbortSignal 取消 → CANCELLED（不当作网络失败自动降级）', async () => {
    let source: ReadableStreamDefaultController<Uint8Array> | null = null
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        source = controller
        controller.enqueue(encoder.encode(ndjson(START)))
      },
    })
    mockFetch(
      () =>
        new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8' },
        }),
    )
    const external = new AbortController()

    const promise = streamTurn(BODY, 'turn:g1:2', {
      signal: external.signal,
      onEvent: (event) => {
        if (event.type === 'start') {
          external.abort()
          source?.error(new DOMException('aborted', 'AbortError'))
        }
      },
    })

    const error = (await promise.catch((reason: unknown) => reason)) as ApiClientError
    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('CANCELLED')
  })

  it('整体超时 → TIMEOUT（受 TURN_STREAM_TIMEOUT_MS 控制，不无限卡 busy）', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, rejectFetch) => {
            init?.signal?.addEventListener('abort', () =>
              rejectFetch(new DOMException('aborted', 'AbortError')),
            )
          }),
      ),
    )

    const promise = streamTurn(BODY, 'turn:g1:2', { timeoutMs: 1000 }).catch(
      (reason: unknown) => reason,
    )
    await vi.advanceTimersByTimeAsync(1001)
    const error = (await promise) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('TIMEOUT')
    expect(TURN_STREAM_TIMEOUT_MS).toBeGreaterThan(0)
  })

  it('缺少幂等键直接拒绝（不旁路 Idempotency）', async () => {
    const mock = mockFetch(() => textStreamResponse(ndjson(DONE)))

    const error = (await streamTurn(BODY, '   ').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('INVALID_INPUT')
    expect(mock).not.toHaveBeenCalled()
  })
})
