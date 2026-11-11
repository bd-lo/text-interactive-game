import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  clearLlmSettings,
  getLlmSettings,
  saveLlmSettings,
  testLlmConnection,
} from '@/api/client'

const STATUS = {
  configured: true,
  enabled: true,
  source: 'user',
  provider: 'deepseek',
  model: 'deepseek-flash',
  api_base: 'https://api.deepseek.com',
  masked_key: 'sk-****abcd',
  has_saved_user_key: true,
  detail: '已使用本机保存的 key（.env 开发 key 优先）',
}

const TEST_OK = {
  ok: true,
  latency_ms: 321,
  provider: 'deepseek',
  model: 'deepseek-flash',
  error: null,
}

const TEST_FAIL = {
  ok: false,
  latency_ms: null,
  provider: 'deepseek',
  model: 'deepseek-flash',
  error: '鉴权失败（401）：请检查 API Key 是否正确',
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

describe('LLM 设置 API client（GET/PUT/DELETE /settings/llm + POST /test）', () => {
  it('getLlmSettings：GET 解析 9 个状态字段（含 masked_key=null 的 env 来源）', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        ...STATUS,
        source: 'env',
        masked_key: null,
        has_saved_user_key: false,
        detail: '正在使用 .env 里的开发 key',
      }),
    )

    const status = await getLlmSettings()

    expect(status.source).toBe('env')
    expect(status.masked_key).toBeNull()
    expect(status.configured).toBe(true)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/settings/llm')
    expect(call[1]?.method).toBe('GET')
    expect(call[1]?.body).toBeUndefined()
  })

  it('getLlmSettings 字段漂移 → 契约错误（不裸 as T）', async () => {
    const missingDetail: Record<string, unknown> = { ...STATUS }
    delete missingDetail.detail
    mockFetch(() => jsonResponse(200, missingDetail))

    const error = (await getLlmSettings().catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.message).toContain('契约')
  })

  it('saveLlmSettings：PUT 完整 body（无 Idempotency-Key），解析 {status, test} 两种形态', async () => {
    const bodies: unknown[] = []
    const mock = mockFetch((_url, init) => {
      bodies.push(JSON.parse(String(init?.body)))
      return jsonResponse(200, { status: STATUS, test: TEST_OK })
    })

    const response = await saveLlmSettings({
      api_key: 'sk-1234567890',
      provider: 'deepseek',
      model: 'deepseek-flash',
      api_base: 'https://api.deepseek.com',
      run_test: true,
    })

    expect(bodies).toEqual([
      {
        api_key: 'sk-1234567890',
        provider: 'deepseek',
        model: 'deepseek-flash',
        api_base: 'https://api.deepseek.com',
        run_test: true,
      },
    ])
    expect(response.status.masked_key).toBe('sk-****abcd')
    expect(response.test?.latency_ms).toBe(321)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/settings/llm')
    expect(call[1]?.method).toBe('PUT')
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Idempotency-Key')).toBeNull() // 按值幂等，不需要幂等键
  })

  it('saveLlmSettings：test=null 形态；422 错误信封 → ApiClientError(INVALID_INPUT)', async () => {
    mockFetch(() => jsonResponse(200, { status: STATUS, test: null }))
    const response = await saveLlmSettings({ api_key: 'sk-x' })
    expect(response.test).toBeNull()

    mockFetch(() =>
      jsonResponse(422, {
        ok: false,
        error_code: 'INVALID_INPUT',
        message: '请求参数校验失败',
        violations: [{ loc: ['body', 'api_key'], type: 'too_short', msg: '长度不足' }],
      }),
    )
    const error = (await saveLlmSettings({ api_key: 'x' }).catch(
      (reason: unknown) => reason,
    )) as ApiClientError
    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(422)
    expect(error.code).toBe('INVALID_INPUT')
  })

  it('testLlmConnection：POST 无 body，解析成功/失败两种结果', async () => {
    const mock = mockFetch(() => jsonResponse(200, TEST_OK))
    const ok = await testLlmConnection()
    expect(ok.ok).toBe(true)
    expect(ok.latency_ms).toBe(321)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/settings/llm/test')
    expect(call[1]?.method).toBe('POST')

    mockFetch(() => jsonResponse(200, TEST_FAIL))
    const failed = await testLlmConnection()
    expect(failed.ok).toBe(false)
    expect(failed.latency_ms).toBeNull()
    expect(failed.error).toContain('401')
  })

  it('clearLlmSettings：DELETE 解析 {status}；后端 500 错误信封冒泡', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        status: {
          ...STATUS,
          source: 'none',
          configured: false,
          enabled: false,
          has_saved_user_key: false,
          masked_key: null,
        },
      }),
    )

    const response = await clearLlmSettings()
    expect(response.status.source).toBe('none')
    expect(response.status.has_saved_user_key).toBe(false)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/settings/llm')
    expect(call[1]?.method).toBe('DELETE')

    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: '凭证文件写入失败' }))
    const error = (await clearLlmSettings().catch((reason: unknown) => reason)) as ApiClientError
    expect(error.status).toBe(500)
  })
})
