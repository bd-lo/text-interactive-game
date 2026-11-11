import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import SettingsView from '@/views/SettingsView.vue'

const BASE_STATUS = {
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
  ...BASE_STATUS,
  source: 'user',
  model: 'deepseek-flash',
  masked_key: 'sk-****user',
  has_saved_user_key: true,
  detail: '已使用本机保存的 key；.env 开发 key 优先',
}

const NONE_STATUS = {
  ...BASE_STATUS,
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

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/settings', name: 'settings', component: SettingsView },
    ],
  })
}

async function mountSettings(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/settings')
  await router.isReady()
  const wrapper = mount(SettingsView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router, mock }
}

function statusHandler(status: unknown): Handler {
  return (url) => {
    if (url === '/api/v1/settings/llm') {
      return jsonResponse(200, status)
    }
    return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
  }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('设置页（/settings：LLM API Key 状态/表单/测试/清除）', () => {
  it('状态卡三种来源：开发环境 .env / 本机保存 / 未配置 分别渲染', async () => {
    const env = await mountSettings(statusHandler(BASE_STATUS))
    expect(env.wrapper.text()).toContain('开发环境 .env')
    expect(env.wrapper.text()).toContain('deepseek-chat')
    expect(env.wrapper.text()).toContain('sk-****env1')
    expect(env.wrapper.text()).toContain('正在使用 .env 里的开发 key')

    const user = await mountSettings(statusHandler(USER_STATUS))
    expect(user.wrapper.text()).toContain('本机保存')
    expect(user.wrapper.text()).toContain('sk-****user')
    expect(user.wrapper.text()).toContain('已启用')

    const none = await mountSettings(statusHandler(NONE_STATUS))
    expect(none.wrapper.text()).toContain('未配置')
    expect(none.wrapper.text()).toContain('未启用（离线模板叙事）')
    expect(none.wrapper.text()).toContain('（未保存）')
    expect(none.wrapper.text()).toContain('未配置大模型，当前为离线模板叙事')
  })

  it('保存并启用：PUT 完整 payload，成功后刷新状态卡并清空密码框', async () => {
    const bodies: unknown[] = []
    const { wrapper } = await mountSettings((url, init) => {
      if (url === '/api/v1/settings/llm' && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse(200, NONE_STATUS)
      }
      if (url === '/api/v1/settings/llm' && init?.method === 'PUT') {
        bodies.push(JSON.parse(String(init.body)))
        return jsonResponse(200, { status: USER_STATUS, test: null })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    await wrapper.find('input[type="password"]').setValue('sk-player-key')
    await wrapper.find('input[placeholder="deepseek-flash"]').setValue('deepseek-chat')
    await wrapper.find('input[placeholder="https://api.deepseek.com"]').setValue(
      'https://api.deepseek.com/v1',
    )
    await wrapper.find('form.llm-form').trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([
      {
        api_key: 'sk-player-key',
        provider: 'deepseek',
        model: 'deepseek-chat',
        api_base: 'https://api.deepseek.com/v1',
      },
    ])
    const text = wrapper.text()
    expect(text).toContain('已保存并启用')
    expect(text).toContain('sk-****user')
    // 安全：保存成功后明文不留在密码框
    expect((wrapper.find('input[type="password"]').element as HTMLInputElement).value).toBe('')
  })

  it('空 key 本地拦下：给中文提示且不发 PUT', async () => {
    const mock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(200, NONE_STATUS),
    )
    vi.stubGlobal('fetch', mock)
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = makeRouter()
    await router.push('/settings')
    await router.isReady()
    const wrapper = mount(SettingsView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    await wrapper.find('form.llm-form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('请先填 DeepSeek API Key')
    const putCalls = mock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined
      return init?.method === 'PUT'
    })
    expect(putCalls).toHaveLength(0)
  })

  it('测试连接：成功展示延迟；失败展示人话原因', async () => {
    const ok = await mountSettings((url, init) => {
      if (url === '/api/v1/settings/llm') {
        return jsonResponse(200, USER_STATUS)
      }
      if (url === '/api/v1/settings/llm/test' && init?.method === 'POST') {
        return jsonResponse(200, TEST_OK)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    const testButton = ok.wrapper.findAll('button').find((button) => button.text() === '测试连接')
    await testButton?.trigger('click')
    await flushPromises()
    expect(ok.wrapper.text()).toContain('连接成功')
    expect(ok.wrapper.text()).toContain('180 毫秒')

    const fail = await mountSettings((url, init) => {
      if (url === '/api/v1/settings/llm') {
        return jsonResponse(200, USER_STATUS)
      }
      if (url === '/api/v1/settings/llm/test' && init?.method === 'POST') {
        return jsonResponse(200, { ...TEST_OK, ok: false, latency_ms: null, error: '鉴权失败（401）' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })
    const failButton = fail.wrapper.findAll('button').find((button) => button.text() === '测试连接')
    await failButton?.trigger('click')
    await flushPromises()
    expect(fail.wrapper.text()).toContain('连接失败')
    expect(fail.wrapper.text()).toContain('鉴权失败（401）')
  })

  it('清除本机 key 需二次确认；确认后 DELETE 并刷新为未配置', async () => {
    const calls: string[] = []
    const { wrapper } = await mountSettings((url, init) => {
      if (url === '/api/v1/settings/llm' && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse(200, USER_STATUS)
      }
      if (url === '/api/v1/settings/llm' && init?.method === 'DELETE') {
        calls.push('DELETE')
        return jsonResponse(200, { status: NONE_STATUS })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    const clearButton = wrapper.findAll('button').find((button) => button.text() === '清除本机 key')
    await clearButton?.trigger('click')
    expect(wrapper.text()).toContain('确认清除（不可恢复）')
    expect(calls).toEqual([])

    const confirm = wrapper.findAll('button').find((button) => button.text().includes('确认清除'))
    await confirm?.trigger('click')
    await flushPromises()

    expect(calls).toEqual(['DELETE'])
    expect(wrapper.text()).toContain('已清除本机保存的 key')
    expect(wrapper.text()).toContain('未配置大模型，当前为离线模板叙事')
    const clearAgain = wrapper.findAll('button').find((button) => button.text() === '清除本机 key')
    expect(clearAgain?.attributes('disabled')).toBeDefined()
  })

  it('状态加载失败：ErrorBanner 展示可重试，重试成功后出现状态卡', async () => {
    let calls = 0
    const { wrapper } = await mountSettings((url) => {
      if (url === '/api/v1/settings/llm') {
        calls += 1
        if (calls === 1) {
          return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'db down' })
        }
        return jsonResponse(200, BASE_STATUS)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    expect(wrapper.find('.error-banner').text()).toContain('出了点问题')
    const retry = wrapper.findAll('button').find((button) => button.text() === '重试')
    await retry?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('开发环境 .env')
    expect(wrapper.text()).toContain('正在使用 .env 里的开发 key')
  })
})
