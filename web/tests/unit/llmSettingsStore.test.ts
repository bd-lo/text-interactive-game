import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLlmSettingsStore } from '@/stores/llmSettings'

const ENV_STATUS = {
  configured: true,
  enabled: true,
  source: 'env',
  provider: 'deepseek',
  model: 'deepseek-chat',
  api_base: 'https://api.deepseek.com',
  masked_key: 'sk-****env1',
  has_saved_user_key: false,
  detail: '正在使用 .env 里的开发 key',
}

const USER_STATUS = {
  ...ENV_STATUS,
  source: 'user',
  model: 'deepseek-flash',
  masked_key: 'sk-****user',
  has_saved_user_key: true,
  detail: '已使用本机保存的 key',
}

const NONE_STATUS = {
  ...ENV_STATUS,
  configured: false,
  enabled: false,
  source: 'none',
  masked_key: null,
  has_saved_user_key: false,
  detail: '未配置大模型，当前为离线模板叙事',
}

const TEST_OK = {
  ok: true,
  latency_ms: 180,
  provider: 'deepseek',
  model: 'deepseek-flash',
  error: null,
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

function unexpected(url: string): Response {
  return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('llmSettings store（状态/保存/测试/清除）', () => {
  it('load：成功写入 status，canClear 随 has_saved_user_key 变化', async () => {
    mockFetch((url) => (url === '/api/v1/settings/llm' ? jsonResponse(200, USER_STATUS) : unexpected(url)))
    const store = useLlmSettingsStore()

    await store.load()

    expect(store.status?.source).toBe('user')
    expect(store.canClear).toBe(true)
    expect(store.configured).toBe(true)
    expect(store.loading).toBe(false)
    expect(store.loadError).toBeNull()
  })

  it('load 失败：错误转玩家可读，status 保持 null（视图手动重试）', async () => {
    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' }))
    const store = useLlmSettingsStore()

    await expect(store.load()).rejects.toBeTruthy()

    expect(store.status).toBeNull()
    expect(store.loadError?.title).toBe('出了点问题')
    expect(store.loading).toBe(false)
  })

  it('save：成功用服务端 status 覆盖并给成功提示；run_test 返回的 test 写入 lastTest', async () => {
    const bodies: unknown[] = []
    mockFetch((url, init) => {
      if (url === '/api/v1/settings/llm' && init?.method === 'PUT') {
        bodies.push(JSON.parse(String(init.body)))
        return jsonResponse(200, { status: USER_STATUS, test: TEST_OK })
      }
      return unexpected(url)
    })
    const store = useLlmSettingsStore()

    const saved = await store.save({ api_key: 'sk-123', run_test: true })

    expect(saved).toBe(true)
    expect(bodies).toEqual([{ api_key: 'sk-123', run_test: true }])
    expect(store.status?.masked_key).toBe('sk-****user')
    expect(store.lastTest?.latency_ms).toBe(180)
    expect(store.saveNotice).toContain('已保存并启用')
    expect(store.saveNotice).toContain('本机保存')
    expect(store.saveError).toBeNull()
  })

  it('save 失败：返回 false 并写 saveError，不清空旧 status', async () => {
    mockFetch((url, init) => {
      if (url === '/api/v1/settings/llm' && init?.method === 'GET') {
        return jsonResponse(200, NONE_STATUS)
      }
      return jsonResponse(422, {
        ok: false,
        error_code: 'INVALID_INPUT',
        message: 'API Key 不能为空',
      })
    })
    const store = useLlmSettingsStore()
    await store.load()

    const saved = await store.save({ api_key: '   ' })

    expect(saved).toBe(false)
    expect(store.saveError?.title).toBeTruthy()
    expect(store.status?.source).toBe('none')
  })

  it('test：成功/失败分别写 lastTest 与 testError', async () => {
    let mode: 'ok' | 'fail' | 'error' = 'ok'
    mockFetch((url) => {
      if (url !== '/api/v1/settings/llm/test') {
        return unexpected(url)
      }
      if (mode === 'ok') {
        return jsonResponse(200, TEST_OK)
      }
      if (mode === 'fail') {
        return jsonResponse(200, { ...TEST_OK, ok: false, latency_ms: null, error: '鉴权失败（401）' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
    })
    const store = useLlmSettingsStore()

    expect(await store.test()).toBe(true)
    expect(store.lastTest?.ok).toBe(true)

    mode = 'fail'
    expect(await store.test()).toBe(true)
    expect(store.lastTest?.ok).toBe(false)
    expect(store.lastTest?.error).toContain('401')

    mode = 'error'
    expect(await store.test()).toBe(false)
    expect(store.lastTest).toBeNull()
    expect(store.testError?.title).toBeTruthy()
  })

  it('clear：成功后用服务端 status 覆盖并提示离线；失败写 clearError', async () => {
    mockFetch((url, init) => {
      if (url === '/api/v1/settings/llm' && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse(200, USER_STATUS)
      }
      if (url === '/api/v1/settings/llm' && init?.method === 'DELETE') {
        return jsonResponse(200, { status: NONE_STATUS })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
    })
    const store = useLlmSettingsStore()
    await store.load()
    store.armClear()
    expect(store.clearArmed).toBe(true)

    expect(await store.clear()).toBe(true)
    expect(store.status?.configured).toBe(false)
    expect(store.canClear).toBe(false)
    expect(store.clearNotice).toContain('离线模板叙事')
    expect(store.clearArmed).toBe(false)
    expect(store.lastTest).toBeNull()
  })

  it('clear 存在 .env 开发 key 时提示仍在用 env；清除失败保留 status 并写错误', async () => {
    mockFetch((url, init) => {
      if (url === '/api/v1/settings/llm' && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse(200, USER_STATUS)
      }
      if (url === '/api/v1/settings/llm' && init?.method === 'DELETE') {
        return jsonResponse(200, { status: ENV_STATUS })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
    })
    const store = useLlmSettingsStore()
    await store.load()
    await store.clear()
    expect(store.clearNotice).toContain('.env')

    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: '凭证文件写入失败' }))
    expect(await store.clear()).toBe(false)
    expect(store.clearError?.title).toBeTruthy()
    expect(store.status?.source).toBe('env')
  })
})
