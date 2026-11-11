import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { GamePlayerViewResponse } from '@/api/types'
import { useGameStore } from '@/stores/game'
import GameView from '@/views/GameView.vue'
import HomeView from '@/views/HomeView.vue'

const STATS = { rooms: 2, items: 1, npcs: 1, objectives: 1, endings: 1, suggestions: 0 }

const SCENARIO = {
  scenario_id: 'scn_first',
  slug: 'tiny-tower',
  title: '小塔',
  author: '示例',
  description: '两房间最小可玩场景',
  schema_version: 1,
  created_at: '2026-11-11T10:00:00Z',
  summary: STATS,
  warnings: [],
}

const SCENARIO_DETAIL = {
  ...SCENARIO,
  definition: {
    schema_version: 1,
    slug: 'tiny-tower',
    meta: { title: '小塔' },
    player: { location: 'gate' },
    rooms: [{ key: 'gate', name: '塔门', description: '一扇虚掩的木门。' }],
  },
}

const WORLD_BOOK = {
  world_book_id: 'wb_abc',
  name: '余烬哨站设定',
  description: '哨站与信标。',
  source: 'tavern',
  entry_count: 12,
  updated_at: '2026-11-11T01:00:00Z',
}

const CREATED = { game_id: 'g1', turn: 1, version: 1, snapshot_version: 1, save_seed: 'save-1' }

const VIEW: GamePlayerViewResponse = {
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

function stubFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

function makeHomeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      { path: '/scenarios', name: 'scenarios', component: { template: '<div>scenarios</div>' } },
      {
        path: '/scenarios/import',
        name: 'scenario-import',
        component: { template: '<div>scenario-import</div>' },
      },
      { path: '/cards', name: 'cards', component: { template: '<div>cards</div>' } },
      { path: '/cards/generate', name: 'card-generate', component: { template: '<div>gen</div>' } },
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
  const mock = stubFetch(handler)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeHomeRouter()
  await router.push('/')
  await router.isReady()
  const wrapper = mount(HomeView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router, mock }
}

function homeHandler(bodies: unknown[], scenariosOk = true): Handler {
  return (url, init) => {
    if (url.startsWith('/api/v1/games?')) {
      return jsonResponse(200, { games: [], total: 0 })
    }
    if (url === '/api/v1/scenarios?limit=50') {
      return scenariosOk
        ? jsonResponse(200, { scenarios: [SCENARIO], total: 1 })
        : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'db down' })
    }
    if (url === '/api/v1/world-books?limit=50') {
      return jsonResponse(200, { world_books: [WORLD_BOOK], total: 1 })
    }
    if (url === '/api/v1/games' && init?.method === 'POST') {
      bodies.push(JSON.parse(String(init.body)))
      return jsonResponse(201, CREATED)
    }
    if (url === '/api/v1/games/g1/view') {
      return jsonResponse(200, VIEW)
    }
    return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
  }
}

function makeGameRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/game/:id', name: 'game', component: GameView },
      { path: '/game/:id/replay', name: 'replay', component: { template: '<div>replay</div>' } },
    ],
  })
}

async function mountGame(path: string, patch: Record<string, unknown>, handler: Handler = () => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })) {
  const mock = stubFetch(handler)
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useGameStore()
  store.$patch((state) => {
    Object.assign(
      state,
      {
        gameId: 'g1',
        turn: 1,
        version: 1,
        snapshotVersion: 1,
        status: 'active',
        scenario: 'story',
        view: VIEW,
      },
      patch,
    )
  })
  const router = makeGameRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(GameView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, store, mock }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('HomeView 场景下拉（内置 + 已导入场景）', () => {
  it('已导入场景出现在下拉里，选中后建局 body={scenario_id} 并带 scenario_id 跳对局页', async () => {
    const bodies: unknown[] = []
    const { wrapper, router } = await mountHome(homeHandler(bodies))

    expect(wrapper.find('optgroup[label="我导入的场景"]').exists()).toBe(true)
    const choices = wrapper.findAll('option').map((option) => option.text())
    expect(choices.some((label) => label.includes('小塔（示例）'))).toBe(true)

    const scenarioSelect = wrapper.findAll('select')[0]
    await scenarioSelect?.setValue('scn_first')
    await wrapper.find('form.home-form').trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([{ scenario_id: 'scn_first' }])
    expect(router.currentRoute.value.name).toBe('game')
    expect(router.currentRoute.value.params.id).toBe('g1')
    expect(router.currentRoute.value.query.scenario_id).toBe('scn_first')
  })

  it('自定义场景可同时绑定世界书（P2 组合：scenario_id + world_book_id）', async () => {
    const bodies: unknown[] = []
    const { wrapper } = await mountHome(homeHandler(bodies))

    await wrapper.findAll('select')[0]?.setValue('scn_first')
    await wrapper.findAll('select')[1]?.setValue('wb_abc')
    await wrapper.find('form.home-form').trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([{ scenario_id: 'scn_first', world_book_id: 'wb_abc' }])
  })

  it('场景列表读取失败时优雅降级：只显示内置场景，仍能正常建局', async () => {
    const bodies: unknown[] = []
    const { wrapper } = await mountHome(homeHandler(bodies, false))

    expect(wrapper.find('optgroup[label="我导入的场景"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂时读不到导入的场景')
    const choices = wrapper.findAll('option').map((option) => option.text())
    expect(choices).toContain('余烬哨站（剧情）')

    await wrapper.find('form.home-form').trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([{ scenario: 'story' }])
  })
})

describe('GameView 场景徽章（内置/自定义场景标题）', () => {
  it('内置场景：徽章显示固定中文名，不请求场景详情', async () => {
    const { wrapper, mock } = await mountGame('/game/g1', { scenario: 'story' })

    expect(wrapper.find('.scenario-badge').text()).toBe('余烬哨站（剧情）')
    expect(mock.mock.calls.filter((call) => String(call[0]).includes('/api/v1/scenarios'))).toEqual([])
  })

  it('自定义场景（本会话建局）：徽章用场景详情标题', async () => {
    const { wrapper } = await mountGame(
      '/game/g1',
      { scenario: 'custom', customScenarioId: 'scn_first' },
      (url) => {
        if (url === '/api/v1/scenarios/scn_first') {
          return jsonResponse(200, SCENARIO_DETAIL)
        }
        return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
      },
    )

    expect(wrapper.find('.scenario-badge').text()).toBe('自定义场景：小塔')
  })

  it('自定义场景（刷新/深链）：从 ?scenario_id= 取 id 并解析标题', async () => {
    const { wrapper, mock } = await mountGame(
      '/game/g1?scenario_id=scn_first',
      { scenario: 'custom' },
      (url) => {
        if (url === '/api/v1/scenarios/scn_first') {
          return jsonResponse(200, SCENARIO_DETAIL)
        }
        return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
      },
    )

    expect(wrapper.find('.scenario-badge').text()).toBe('自定义场景：小塔')
    expect(String(mock.mock.calls[0]?.[0])).toBe('/api/v1/scenarios/scn_first')
  })

  it('自定义场景标题取不到（已删/后端不可用）：徽章回退「自定义场景」，不影响对局', async () => {
    const { wrapper } = await mountGame(
      '/game/g1',
      { scenario: 'custom', customScenarioId: 'scn_gone' },
      () => jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: 'not found' }),
    )

    expect(wrapper.find('.scenario-badge').text()).toBe('自定义场景')
    expect(wrapper.find('.game-head').text()).toContain('塔门')
  })
})
