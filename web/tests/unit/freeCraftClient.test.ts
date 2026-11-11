import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  getFreeCraftSettings,
  saveFreeCraftSettings,
} from '@/api/client'

const FREE_CRAFT_OFF = {
  enabled: false,
  available: true,
  llm_configured: true,
  llm_enabled: true,
  risk_accepted: false,
  source: 'user',
  detail: '自由制造未开启：未命中配方时会返回不支持。',
}

const FREE_CRAFT_ON = {
  ...FREE_CRAFT_OFF,
  enabled: true,
  risk_accepted: true,
  detail: '自由制造已开启：未命中配方时会调用大模型生成草稿，可能产生额外费用。',
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
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('自由制造设置 API client（GET/PUT /settings/free-craft）', () => {
  it('getFreeCraftSettings：GET 解析 7 个状态字段', async () => {
    const mock = mockFetch(() => jsonResponse(200, FREE_CRAFT_OFF))

    const status = await getFreeCraftSettings()

    expect(status).toEqual(FREE_CRAFT_OFF)
    const [call] = mock.mock.calls
    expect(String(call[0])).toBe('/api/v1/settings/free-craft')
    expect((call[1] as RequestInit | undefined)?.method).toBe('GET')
  })

  it('getFreeCraftSettings：字段漂移 → 契约错误（不裸 as T）', async () => {
    mockFetch(() => jsonResponse(200, { ...FREE_CRAFT_OFF, source: 'browser' }))

    const error = (await getFreeCraftSettings().catch(
      (reason: unknown) => reason,
    )) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.code).toBe('INTERNAL')
    expect(error.message).toContain('前端契约不符')
  })

  it('saveFreeCraftSettings：PUT 完整 body 且不带 Idempotency-Key', async () => {
    const mock = mockFetch(() => jsonResponse(200, FREE_CRAFT_ON))

    const status = await saveFreeCraftSettings({
      enabled: true,
      accept_resolver_llm_risk: true,
    })

    expect(status).toEqual(FREE_CRAFT_ON)
    const [call] = mock.mock.calls
    expect(String(call[0])).toBe('/api/v1/settings/free-craft')
    const init = call[1] as RequestInit
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      enabled: true,
      accept_resolver_llm_risk: true,
    })
    expect(new Headers(init.headers).has('Idempotency-Key')).toBe(false)
  })

  it('saveFreeCraftSettings：409 统一信封 → ApiClientError(CONFLICT)', async () => {
    mockFetch(() =>
      jsonResponse(409, {
        ok: false,
        error_code: 'CONFLICT',
        message: '请先配置并启用大模型 API Key，再开启自由制造',
      }),
    )

    const error = (await saveFreeCraftSettings({
      enabled: true,
      accept_resolver_llm_risk: true,
    }).catch((reason: unknown) => reason)) as ApiClientError

    expect(error.status).toBe(409)
    expect(error.code).toBe('CONFLICT')
    expect(error.message).toContain('API Key')
  })
})
