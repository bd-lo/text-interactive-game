import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ScenarioLibraryView from '@/views/ScenarioLibraryView.vue'

const STATS = { rooms: 2, items: 1, npcs: 1, objectives: 1, endings: 1, suggestions: 0 }

function summary(id: string, title: string, author = '示例') {
  return {
    scenario_id: id,
    slug: id.replace('scn_', ''),
    title,
    author,
    description: `${title}的一句话简介`,
    schema_version: 1,
    created_at: '2026-11-11T10:00:00Z',
    summary: STATS,
    warnings: [],
  }
}

const FIRST = summary('scn_first', '小塔')
const SECOND = summary('scn_second', '余烬哨站')

const CREATED = { game_id: 'g1', turn: 1, version: 1, snapshot_version: 1, save_seed: 'save-1' }

const PLAYER_VIEW = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  status: 'active',
  scenario: 'custom',
  hp: 10,
  max_hp: 10,
  inventory: [],
  room: { room_id: 'gate', title: '塔门', description: '一扇虚掩的木门。', exits: {}, items: [], npcs: [] },
  goal: null,
  ending_text: null,
  suggested_actions: [],
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
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      { path: '/scenarios', name: 'scenarios', component: ScenarioLibraryView },
      { path: '/scenarios/import', name: 'scenario-import', component: { template: '<div>import</div>' } },
      {
        path: '/scenarios/:scenarioId',
        name: 'scenario-detail',
        component: { template: '<div>detail</div>' },
      },
    ],
  })
}

async function mountLibrary(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/scenarios')
  await router.isReady()
  const wrapper = mount(ScenarioLibraryView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router, mock }
}

function listHandler(urls: string[], total = 45): Handler {
  return (url) => {
    if (url.startsWith('/api/v1/scenarios?')) {
      urls.push(url)
      return jsonResponse(200, { scenarios: [FIRST, SECOND], total })
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

describe('场景库列表页（/scenarios）', () => {
  it('渲染标题/作者/统计摘要/导入时间，并支持搜索与手动翻页', async () => {
    const urls: string[] = []
    const { wrapper } = await mountLibrary(listHandler(urls))

    const text = wrapper.text()
    expect(text).toContain('场景库')
    expect(text).toContain('小塔')
    expect(text).toContain('作者：示例')
    expect(text).toContain('2 个房间')
    expect(text).toContain('1 个结局')
    expect(text).toContain('导入于 2026-11-11 10:00:00 UTC')
    expect(text).toContain('共 45 个场景')

    const searchInput = wrapper.find('input[type="text"]')
    await searchInput.setValue('余烬')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(urls[urls.length - 1]).toContain('q=%E4%BD%99%E7%83%AC')

    const next = wrapper.findAll('button').find((button) => button.text().includes('下一页'))
    await next?.trigger('click')
    await flushPromises()
    expect(urls[urls.length - 1]).toContain('offset=20')

    const prev = wrapper.findAll('button').find((button) => button.text().includes('上一页'))
    await prev?.trigger('click')
    await flushPromises()
    expect(urls[urls.length - 1]).toContain('offset=0')
  })

  it('空态：引导去导入场景 JSON', async () => {
    const { wrapper } = await mountLibrary(() =>
      jsonResponse(200, { scenarios: [], total: 0 }),
    )

    expect(wrapper.text()).toContain('还没有导入的场景')
    expect(wrapper.text()).toContain('导入一份场景 JSON')
  })

  it('列表读取失败：ErrorBanner 展示玩家可读错误并可重试', async () => {
    const { wrapper } = await mountLibrary(() =>
      jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'db down' }),
    )

    const banner = wrapper.find('.error-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('出了点问题')
  })

  it('删除需二次确认：第一次只武装按钮，第二次才发 DELETE 并移除条目', async () => {
    const calls: string[] = []
    const { wrapper } = await mountLibrary((url, init) => {
      const method = init?.method ?? 'GET'
      if (url.startsWith('/api/v1/scenarios?')) {
        return jsonResponse(200, { scenarios: [FIRST, SECOND], total: 2 })
      }
      if (url === '/api/v1/scenarios/scn_first' && method === 'DELETE') {
        calls.push('DELETE scn_first')
        return new Response(null, { status: 204 })
      }
      calls.push(`${method} ${url}`)
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    const deleteButton = wrapper.findAll('button').find((button) => button.text() === '删除')
    await deleteButton?.trigger('click')
    expect(wrapper.text()).toContain('确认删除（不可恢复）')
    expect(calls.filter((call) => call.startsWith('DELETE'))).toEqual([])

    const confirm = wrapper.findAll('button').find((button) => button.text().includes('确认删除'))
    await confirm?.trigger('click')
    await flushPromises()

    expect(calls).toContain('DELETE scn_first')
    expect(wrapper.text()).not.toContain('小塔')
    expect(wrapper.text()).toContain('余烬哨站')
  })

  it('按此场景开局：POST /games {scenario_id} 并带 scenario_id 跳对局页', async () => {
    const bodies: unknown[] = []
    let gameCallUrl = ''
    const { wrapper, router, mock } = await mountLibrary((url, init) => {
      if (url.startsWith('/api/v1/scenarios?')) {
        return jsonResponse(200, { scenarios: [FIRST], total: 1 })
      }
      if (url === '/api/v1/games' && init?.method === 'POST') {
        bodies.push(JSON.parse(String(init.body)))
        return jsonResponse(201, CREATED)
      }
      if (url === '/api/v1/games/g1/view') {
        return jsonResponse(200, PLAYER_VIEW)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    const start = wrapper.findAll('button').find((button) => button.text().includes('按此场景开局'))
    await start?.trigger('click')
    await flushPromises()

    expect(bodies).toEqual([{ scenario_id: 'scn_first' }])
    const gameCall = mock.mock.calls.find((call) => String(call[0]) === '/api/v1/games')
    expect(gameCall).toBeDefined()
    gameCallUrl = String(gameCall?.[0])
    expect(gameCallUrl).toBe('/api/v1/games')
    expect(router.currentRoute.value.name).toBe('game')
    expect(router.currentRoute.value.params.id).toBe('g1')
    expect(router.currentRoute.value.query.scenario_id).toBe('scn_first')
  })
})
