import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useFreeCraftStore } from '@/stores/freeCraft'

const OFF = {
  enabled: false,
  available: true,
  llm_configured: true,
  llm_enabled: true,
  risk_accepted: false,
  source: 'user',
  detail: '自由制造未开启：未命中配方时会返回不支持。',
}

const ON = {
  ...OFF,
  enabled: true,
  risk_accepted: true,
  detail: '自由制造已开启：未命中配方时会在进入裁决前调用大模型生成一次草稿，可能产生费用。',
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('自由制造设置 store', () => {
  it('load：读取服务端状态', async () => {
    stubFetch(() => jsonResponse(200, OFF))
    const store = useFreeCraftStore()

    await store.load()

    expect(store.status).toEqual(OFF)
    expect(store.enabled).toBe(false)
    expect(store.canEnable).toBe(true)
  })

  it('开启需二次确认：arm 不发请求，确认后 PUT true', async () => {
    const mock = stubFetch((url, init) => {
      if (url === '/api/v1/settings/free-craft' && init?.method === 'PUT') {
        return jsonResponse(200, ON)
      }
      return jsonResponse(200, OFF)
    })
    const store = useFreeCraftStore()
    await store.load()
    const before = mock.mock.calls.length

    store.armEnable()
    expect(store.confirmArmed).toBe(true)
    expect(mock.mock.calls.length).toBe(before)

    const ok = await store.enable()

    expect(ok).toBe(true)
    expect(store.confirmArmed).toBe(false)
    expect(store.status).toEqual(ON)
    expect(store.saveNotice).toContain('自由制造已开启')
    expect(store.saveNotice).toContain('生成一次草稿（进入裁决前）')
    expect(store.saveNotice).toContain('可能产生费用')
    const [, init] = mock.mock.calls.at(-1) ?? []
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      enabled: true,
      accept_resolver_llm_risk: true,
    })
  })

  it('关闭直接 PUT false，并清除确认态', async () => {
    const mock = stubFetch((url, init) => {
      if (url === '/api/v1/settings/free-craft' && init?.method === 'PUT') {
        return jsonResponse(200, OFF)
      }
      return jsonResponse(200, ON)
    })
    const store = useFreeCraftStore()
    await store.load()
    store.armEnable()

    const ok = await store.disable()

    expect(ok).toBe(true)
    expect(store.confirmArmed).toBe(false)
    expect(store.status).toEqual(OFF)
    const [, init] = mock.mock.calls.at(-1) ?? []
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      enabled: false,
      accept_resolver_llm_risk: false,
    })
  })

  it('保存失败：保留确认态并给出人话错误', async () => {
    stubFetch((url, init) => {
      if (url === '/api/v1/settings/free-craft' && init?.method === 'PUT') {
        return jsonResponse(409, {
          ok: false,
          error_code: 'CONFLICT',
          message: '请先配置并启用大模型 API Key，再开启自由制造',
        })
      }
      return jsonResponse(200, OFF)
    })
    const store = useFreeCraftStore()
    await store.load()
    store.armEnable()

    const ok = await store.enable()

    expect(ok).toBe(false)
    expect(store.confirmArmed).toBe(true)
    expect(store.saveError?.message).toContain('API Key')
    expect(store.status).toEqual(OFF)
  })
})
