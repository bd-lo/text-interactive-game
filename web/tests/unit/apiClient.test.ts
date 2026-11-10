import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClientError, createGame, getGame, getPlayerView, submitTurn } from '@/api/client'

const CREATED = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  save_seed: '42',
}

const TURN_OK = {
  ok: true,
  turn_id: 2,
  version: 2,
  text: '你向北走进地窖。',
  events: [],
  error_code: null,
  effects: [],
  suggested_actions: [],
  usage: {},
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type FetchHandler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: FetchHandler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init))
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('api client（第 6 章 §6.7 错误契约 + 运行时字段校验）', () => {
  it('createGame 携带 Idempotency-Key/JSON 并解析 5 字段', async () => {
    const mock = mockFetch(() => jsonResponse(201, CREATED))

    const created = await createGame({ seed: 42, scenario: 'm2' }, 'create-game:42:m2')

    expect(created).toEqual(CREATED)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/games')
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(call[1]?.body))).toEqual({ seed: 42, scenario: 'm2' })
  })

  it('业务失败信封 → ApiClientError（error_code/status 可用，不抛裸 Error）', async () => {
    mockFetch(() =>
      jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '存档不存在: nope' }),
    )

    const error = await getGame('nope').catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ApiClientError)
    const apiError = error as ApiClientError
    expect(apiError.status).toBe(404)
    expect(apiError.code).toBe('NOT_FOUND')
    expect(apiError.isConflict).toBe(false)
  })

  it('CONFLICT（409）→ isConflict=true（UI 走刷新换新键，而不是自动重试）', async () => {
    mockFetch(() =>
      jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '版本冲突' }),
    )

    const error = (await getPlayerView('g1').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.isConflict).toBe(true)
  })

  it('响应字段缺失 → 契约校验失败（不裸 as T）', async () => {
    mockFetch(() => jsonResponse(201, { game_id: 'g1', turn: 1, version: 1, snapshot_version: 1 }))

    const error = (await createGame({ seed: 1 }, 'create-game:1:m2').catch(
      (reason: unknown) => reason,
    )) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('INTERNAL')
    expect(error.message).toContain('契约')
  })

  it('submitTurn：路径 /api/v1/turns、字段 input/expected_version、幂等键', async () => {
    const mock = mockFetch(() => jsonResponse(200, TURN_OK))

    const result = await submitTurn({ game_id: 'g1', input: '向北走', expected_version: 1 }, 'turn:g1:1')

    expect(result.version).toBe(2)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/turns')
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      game_id: 'g1',
      input: '向北走',
      expected_version: 1,
    })
  })
})
