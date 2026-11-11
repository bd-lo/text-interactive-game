import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  createGame,
  deleteScenario,
  getScenario,
  importScenario,
  listScenarios,
  validateScenario,
} from '@/api/client'

const STATS = { rooms: 2, items: 1, npcs: 1, objectives: 1, endings: 1, suggestions: 0 }

const SUMMARY = {
  scenario_id: 'scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
  slug: 'tiny-tower',
  title: '小塔',
  author: '示例',
  description: '两房间最小可玩场景',
  schema_version: 1,
  created_at: '2026-11-11T10:00:00Z',
  summary: STATS,
  warnings: [],
}

const DEFINITION = {
  schema_version: 1,
  slug: 'tiny-tower',
  meta: { title: '小塔' },
  player: { location: 'gate' },
  rooms: [{ key: 'gate', name: '塔门', description: '一扇虚掩的木门。' }],
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type FetchHandler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: FetchHandler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

beforeEach(() => {
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('场景 API client（scenario modules v1 §3.4）', () => {
  it('listScenarios：q/limit/offset 组装 + {scenarios,total} 解析 + warnings 归一化', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        scenarios: [
          {
            ...SUMMARY,
            warnings: ['旧式字符串告警', { path: 'rooms[0].items', message: '未使用的 item 引用' }],
          },
        ],
        total: 1,
      }),
    )

    const response = await listScenarios({ q: ' 小塔 ', limit: 20, offset: 0 })

    expect(response.total).toBe(1)
    expect(response.scenarios[0]?.title).toBe('小塔')
    expect(response.scenarios[0]?.summary.rooms).toBe(2)
    expect(response.scenarios[0]?.warnings).toEqual([
      { path: '', message: '旧式字符串告警' },
      { path: 'rooms[0].items', message: '未使用的 item 引用' },
    ])
    const url = String((mock.mock.calls[0] as unknown[])[0])
    expect(url).toBe(
      '/api/v1/scenarios?q=%E5%B0%8F%E5%A1%94&limit=20&offset=0',
    )
  })

  it('listScenarios 字段漂移 → 契约错误（不裸 as T）', async () => {
    mockFetch(() =>
      jsonResponse(200, {
        scenarios: [{ ...SUMMARY, summary: { rooms: 1, items: 0, npcs: 0, objectives: 0, endings: 0 } }],
        total: 1,
      }),
    )

    const error = (await listScenarios().catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.message).toContain('契约')
  })

  it('getScenario：解析 definition/summary/warnings（未知 404 冒泡）', async () => {
    mockFetch(() =>
      jsonResponse(200, {
        ...SUMMARY,
        definition: DEFINITION,
        warnings: [{ path: '', message: '空 NPC 对话' }],
      }),
    )

    const detail = await getScenario(SUMMARY.scenario_id)

    expect(detail.definition).toEqual(DEFINITION)
    expect(detail.summary.suggestions).toBe(0)
    expect(detail.warnings[0]?.message).toBe('空 NPC 对话')

    mockFetch(() =>
      jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '场景不存在' }),
    )
    const error = (await getScenario('scn_missing').catch((reason: unknown) => reason)) as ApiClientError
    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(404)
    expect(error.code).toBe('NOT_FOUND')
  })

  it('validateScenario：body=场景 JSON 原文（不套 {payload}），解析 ok/errors(path)/warnings/summary', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        ok: false,
        errors: [{ path: 'rooms[2].exits.east', message: '未知房间 key "hub2"' }],
        warnings: [{ path: 'npcs[0].dialogue', message: 'NPC 对话为空，将使用通用文案' }],
        summary: STATS,
      }),
    )

    const response = await validateScenario(DEFINITION)

    expect(response.ok).toBe(false)
    expect(response.errors[0]?.path).toBe('rooms[2].exits.east')
    expect(response.summary.objectives).toBe(1)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/scenarios/validate')
    expect(call[1]?.method).toBe('POST')
    expect(JSON.parse(String(call[1]?.body))).toEqual(DEFINITION)
    // 只读预检：无幂等键
    expect(new Headers(call[1]?.headers).get('Idempotency-Key')).toBeNull()
  })

  it('validateScenario 422 → ApiClientError(INVALID_INPUT)（错误信封原样保留）', async () => {
    mockFetch(() =>
      jsonResponse(422, {
        ok: false,
        error_code: 'INVALID_INPUT',
        message: 'scenario schema violation',
        violations: [{ loc: ['body', 'schema_version'], type: 'missing', msg: '必填' }],
      }),
    )

    const error = (await validateScenario({}).catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(422)
    expect(error.code).toBe('INVALID_INPUT')
    expect(error.violations).toBeDefined()
  })

  it('importScenario：multipart + 幂等键，解析 201（summary/warnings）', async () => {
    const mock = mockFetch(() =>
      jsonResponse(201, {
        scenario_id: 'scn_new',
        slug: 'tiny-tower',
        title: '小塔',
        summary: { ...STATS, suggestions: 3 },
        warnings: [{ path: 'items[0]', message: '未使用的物品 key' }],
      }),
    )

    const response = await importScenario(JSON.stringify(DEFINITION), 'scn-import-key', 'tiny-tower.json')

    expect(response.scenario_id).toBe('scn_new')
    expect(response.summary.suggestions).toBe(3)
    expect(response.warnings[0]?.path).toBe('items[0]')
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/scenarios')
    expect(call[1]?.method).toBe('POST')
    expect(call[1]?.body).toBeInstanceOf(FormData)
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(headers.get('Content-Type')).toBeNull() // 浏览器自动带 multipart boundary
    const form = call[1]?.body as FormData
    expect((form.get('file') as File).name).toBe('tiny-tower.json')
  })

  it('importScenario 409（同键异参）→ ApiClientError.isConflict', async () => {
    mockFetch(() =>
      jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: 'idempotency key reused' }),
    )

    const error = (await importScenario('{}', 'scn-import-key').catch(
      (reason: unknown) => reason,
    )) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.isConflict).toBe(true)
  })

  it('deleteScenario：DELETE + 幂等键，204 空 body 正常返回（重复删除 404 冒泡）', async () => {
    const mock = mockFetch(() => new Response(null, { status: 204 }))

    await expect(deleteScenario('scn_old', 'scn-delete-key')).resolves.toBeUndefined()
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/scenarios/scn_old')
    expect(call[1]?.method).toBe('DELETE')
    expect(new Headers(call[1]?.headers).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)

    mockFetch(() => jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '场景不存在' }))
    const error = (await deleteScenario('scn_old', 'scn-delete-key').catch(
      (reason: unknown) => reason,
    )) as ApiClientError
    expect(error.status).toBe(404)
  })

  it('createGame 支持 scenario_id 加法（可与 world_book_id 组合，不与 scenario 同发）', async () => {
    const mock = mockFetch(() =>
      jsonResponse(201, {
        game_id: 'g_custom',
        turn: 1,
        version: 1,
        snapshot_version: 1,
        save_seed: 'seed-1',
      }),
    )

    await createGame(
      { scenario_id: 'scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK', world_book_id: 'wb_1' },
      'create-game-custom',
    )

    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      scenario_id: 'scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
      world_book_id: 'wb_1',
    })
  })
})
