import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import { LLM_BANNER_DISMISSED_KEY } from '@/utils/llm'

const NONE_STATUS = {
  configured: false,
  enabled: false,
  source: 'none',
  provider: 'deepseek',
  model: 'deepseek-flash',
  api_base: 'https://api.deepseek.com',
  masked_key: null,
  has_saved_user_key: false,
  detail: '未配置大模型，当前为离线模板叙事',
}

const CONFIGURED_STATUS = {
  ...NONE_STATUS,
  configured: true,
  enabled: true,
  source: 'user',
  masked_key: 'sk-****user',
  has_saved_user_key: true,
  detail: '已使用本机保存的 key',
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
      { path: '/', name: 'home', component: HomeView },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      { path: '/settings', name: 'settings', component: { template: '<div>settings</div>' } },
      { path: '/cards', name: 'cards', component: { template: '<div>cards</div>' } },
      { path: '/cards/generate', name: 'card-generate', component: { template: '<div>gen</div>' } },
      { path: '/scenarios', name: 'scenarios', component: { template: '<div>scenarios</div>' } },
      {
        path: '/scenarios/import',
        name: 'scenario-import',
        component: { template: '<div>scenario-import</div>' },
      },
      { path: '/world-books', name: 'world-books', component: { template: '<div>wb</div>' } },
      {
        path: '/world-books/import',
        name: 'world-book-import',
        component: { template: '<div>wb-import</div>' },
      },
    ],
  })
}

async function mountHome(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  const wrapper = mount(HomeView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}

function baseHandler(llm: () => Response): Handler {
  return (url) => {
    if (url.startsWith('/api/v1/games?')) {
      return jsonResponse(200, { games: [], total: 0 })
    }
    if (url === '/api/v1/world-books?limit=50') {
      return jsonResponse(200, { world_books: [], total: 0 })
    }
    if (url === '/api/v1/scenarios?limit=50') {
      return jsonResponse(200, { scenarios: [], total: 0 })
    }
    if (url === '/api/v1/settings/llm') {
      return llm()
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

describe('Home 首屏「未配置大模型」横幅', () => {
  it('未配置且未启用时显示横幅与「去设置」入口，可关闭并记住', async () => {
    const { wrapper } = await mountHome(baseHandler(() => jsonResponse(200, NONE_STATUS)))

    const banner = wrapper.find('.llm-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('未配置大模型')
    expect(banner.text()).toContain('离线模板叙事')
    const link = banner.find('a')
    expect(link.attributes('href')).toBe('/settings')

    const close = banner.findAll('button').find((button) => button.text() === '关闭')
    await close?.trigger('click')
    await flushPromises()

    expect(wrapper.find('.llm-banner').exists()).toBe(false)
    expect(localStorage.getItem(LLM_BANNER_DISMISSED_KEY)).toBe('1')

    // 再次打开首页：关闭状态持久化，不再打扰
    const again = await mountHome(baseHandler(() => jsonResponse(200, NONE_STATUS)))
    expect(again.wrapper.find('.llm-banner').exists()).toBe(false)
  })

  it('已配置时（configured=true）不显示横幅', async () => {
    const { wrapper } = await mountHome(baseHandler(() => jsonResponse(200, CONFIGURED_STATUS)))
    expect(wrapper.find('.llm-banner').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('未配置大模型，当前为离线模板叙事。')
  })

  it('状态接口读取失败时不显示横幅（后端不可用不打扰首屏）', async () => {
    const { wrapper } = await mountHome(
      baseHandler(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })),
    )
    expect(wrapper.find('.llm-banner').exists()).toBe(false)
  })
})
