/**
 * N1-032 场景分享（web）：导出客户端 + 详情页「导出 JSON」按钮。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiClientError, exportScenario } from '@/api/client'
import { useScenariosStore } from '@/stores/scenarios'
import ScenarioDetailView from '@/views/ScenarioDetailView.vue'

const EXPORTED = {
  schema_version: 2,
  slug: 'ember-watch',
  meta: { title: '余烬哨站', author: '玩家名' },
  player: { location: 'bridge' },
  rooms: [{ key: 'bridge', name: '断桥', description: '风雪把桥面埋了一半。' }],
  flags: [{ key: 'briefed', when: { in_room: 'bridge' } }],
}

function exportResponse(): Response {
  return new Response(JSON.stringify(EXPORTED), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Content-Disposition': 'attachment; filename="ember-watch.json"',
    },
  })
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

let anchorClick: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:mock-export'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('exportScenario（客户端）', () => {
  it('GET /scenarios/{id}/export：返回文件名 + JSON 原文（只读、无幂等键）', async () => {
    const mock = mockFetch(() => exportResponse())

    const file = await exportScenario('scn_1')

    expect(file.filename).toBe('ember-watch.json')
    expect(JSON.parse(file.json)).toEqual(EXPORTED)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/scenarios/scn_1/export')
    expect(call[1]?.method).toBe('GET')
    expect(new Headers(call[1]?.headers).get('Idempotency-Key')).toBeNull()
  })

  it('Content-Disposition 缺失/非法时回退 <scenario_id>.json', async () => {
    mockFetch(
      () =>
        new Response(JSON.stringify(EXPORTED), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    expect((await exportScenario('scn_2')).filename).toBe('scn_2.json')
  })

  it('404 → ApiClientError(NOT_FOUND)；非 JSON 响应体 → 契约错误', async () => {
    mockFetch(() => jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '场景不存在' }))
    const notFound = (await exportScenario('nope').catch((error: unknown) => error)) as ApiClientError
    expect(notFound).toBeInstanceOf(ApiClientError)
    expect(notFound.status).toBe(404)
    expect(notFound.code).toBe('NOT_FOUND')

    mockFetch(
      () =>
        new Response('<html>oops</html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
    )
    const bad = (await exportScenario('scn_3').catch((error: unknown) => error)) as ApiClientError
    expect(bad).toBeInstanceOf(ApiClientError)
    expect(bad.message).toContain('不是合法 JSON')
  })
})

describe('ScenarioDetailView（导出 JSON / 编辑入口）', () => {
  const DETAIL = {
    scenario_id: 'scn_1',
    slug: 'ember-watch',
    title: '余烬哨站',
    author: '玩家名',
    description: '',
    schema_version: 2,
    created_at: '2026-11-12T00:00:00Z',
    definition: EXPORTED,
    summary: { rooms: 1, items: 0, npcs: 0, objectives: 0, endings: 0, suggestions: 0 },
    warnings: [],
  }

  function makeRouter() {
    return createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/scenarios', name: 'scenarios', component: { template: '<div>library</div>' } },
        {
          path: '/scenarios/:scenarioId',
          name: 'scenario-detail',
          component: ScenarioDetailView,
        },
        { path: '/scenarios/editor', name: 'scenario-editor', component: { template: '<div>editor</div>' } },
        { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      ],
    })
  }

  async function mountDetail() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useScenariosStore()
    store.$patch({ detail: DETAIL })
    vi.spyOn(store, 'loadDetail').mockResolvedValue(undefined)
    const router = makeRouter()
    await router.push('/scenarios/scn_1')
    await router.isReady()
    const wrapper = mount(ScenarioDetailView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    return { wrapper, router }
  }

  it('点「导出 JSON」：请求导出端点并触发浏览器下载、显示提示', async () => {
    const mock = mockFetch(() => exportResponse())
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="scenario-export"]').trigger('click')
    await flushPromises()

    const call = mock.mock.calls.find((entry) =>
      String(entry[0]).includes('/api/v1/scenarios/scn_1/export'),
    )
    expect(call).toBeDefined()
    expect(anchorClick).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('已导出 ember-watch.json')
    wrapper.unmount()
  })

  it('导出失败：错误横幅展示可读原因，不触发下载', async () => {
    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' }))
    const { wrapper } = await mountDetail()

    await wrapper.find('[data-testid="scenario-export"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('boom')
    expect(anchorClick).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('「以此为基础编辑」入口指向 /scenarios/editor?from=<id>', async () => {
    mockFetch(() => exportResponse())
    const { wrapper, router } = await mountDetail()

    const link = wrapper.find('[data-testid="scenario-edit"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toContain('/scenarios/editor')
    expect(link.attributes('href')).toContain('from=scn_1')

    await router.push('/scenarios/editor?from=scn_1')
    expect(router.currentRoute.value.name).toBe('scenario-editor')
    wrapper.unmount()
  })
})
