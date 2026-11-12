import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ScenarioDetailView from '@/views/ScenarioDetailView.vue'

const STATS = { rooms: 2, items: 1, npcs: 1, objectives: 2, endings: 2, suggestions: 1 }

const DEFINITION = {
  schema_version: 1,
  slug: 'ember-watch',
  meta: { title: '余烬哨站（示例）', author: '玩家名', description: '风雪里的哨站。' },
  player: { location: 'bridge', inventory: [] },
  rooms: [
    { key: 'bridge', name: '断桥', description: '风雪把桥面埋了一半。', exits: { east: 'hub' } },
    { key: 'hub', name: '哨站大厅', description: '火塘里剩着余烬。', items: ['flint'] },
  ],
  items: [{ key: 'flint', name: '火石', description: '一块打火石。', tags: ['quest'] }],
  npcs: [{ key: 'watchman', name: '守夜人', room: 'hub' }],
  goal: {
    title: '点亮信号灯',
    description: '让塔顶的火重新烧起来。',
    objectives: [
      { key: 'listen', text: '向守夜人打听情况' },
      { key: 'light', text: '带着火石登上塔顶' },
    ],
  },
  endings: [
    { key: 'victory', kind: 'win', title: '灯亮了', text: '塔顶的风很大。', priority: 10 },
    { key: 'fallen', kind: 'lose', title: '倒在雪里', text: '……', priority: 1 },
  ],
}

const DETAIL = {
  scenario_id: 'scn_first',
  slug: 'ember-watch',
  title: '余烬哨站（示例）',
  author: '玩家名',
  description: '风雪里的哨站。',
  schema_version: 1,
  created_at: '2026-11-11T10:00:00Z',
  definition: DEFINITION,
  summary: STATS,
  warnings: [{ path: 'items[0]', message: '未使用的物品 key' }],
}

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
  room: { room_id: 'bridge', title: '断桥', description: '风雪。', exits: {}, items: [], npcs: [] },
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
      { path: '/scenarios', name: 'scenarios', component: { template: '<div>scenarios</div>' } },
      { path: '/scenarios/editor', name: 'scenario-editor', component: { template: '<div>editor</div>' } },
      { path: '/scenarios/:scenarioId', name: 'scenario-detail', component: ScenarioDetailView },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
    ],
  })
}

async function mountDetail(path: string, handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(ScenarioDetailView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router, mock }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('场景详情页（/scenarios/:id）', () => {
  it('渲染 meta/统计摘要/房间·物品·NPC·目标·结局摘要与 warnings', async () => {
    const { wrapper } = await mountDetail('/scenarios/scn_first', () =>
      jsonResponse(200, DETAIL),
    )

    const text = wrapper.text()
    expect(text).toContain('余烬哨站（示例）')
    expect(text).toContain('作者 玩家名')
    expect(text).toContain('slug ember-watch')
    expect(text).toContain('2 个房间')
    expect(text).toContain('断桥')
    expect(text).toContain('火石')
    expect(text).toContain('守夜人')
    expect(text).toContain('在哨站大厅')
    expect(text).toContain('点亮信号灯')
    expect(text).toContain('向守夜人打听情况')
    expect(text).toContain('胜利')
    expect(text).toContain('灯亮了')
    expect(text).toContain('失败')
    expect(text).toContain('items[0]：未使用的物品 key')
    // 玩家可见文本不出现内部实体 ID（scenario_id 是场景库主键，不在实体 ID 正则内）
    expect(text).not.toMatch(/(?:char|npc|item|room|game)_[0-9A-Z]{6,}/)
  })

  it('原始 JSON 折叠区默认收起，展开后是定义原文（插值展示，无 v-html）', async () => {
    const { wrapper } = await mountDetail('/scenarios/scn_first', () =>
      jsonResponse(200, DETAIL),
    )

    const raw = wrapper.find('.scenario-raw')
    expect(raw.exists()).toBe(true)
    expect(raw.attributes('open')).toBeUndefined()
    const pre = wrapper.find('.scenario-raw-json')
    expect(pre.text()).toContain('"schema_version": 1')
    expect(pre.text()).toContain('"slug": "ember-watch"')
    expect(pre.text()).toContain('"rooms"')
  })

  it('按此场景开局：POST /games {scenario_id} 并带 scenario_id 跳对局页', async () => {
    const bodies: unknown[] = []
    const { wrapper, router } = await mountDetail('/scenarios/scn_first', (url, init) => {
      if (url === '/api/v1/scenarios/scn_first') {
        return jsonResponse(200, DETAIL)
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
    expect(router.currentRoute.value.name).toBe('game')
    expect(router.currentRoute.value.params.id).toBe('g1')
    expect(router.currentRoute.value.query.scenario_id).toBe('scn_first')
  })

  it('删除需二次确认，成功后回场景库', async () => {
    const calls: string[] = []
    const { wrapper, router } = await mountDetail('/scenarios/scn_first', (url, init) => {
      if (url === '/api/v1/scenarios/scn_first' && (init?.method ?? 'GET') === 'GET') {
        return jsonResponse(200, DETAIL)
      }
      if (url === '/api/v1/scenarios/scn_first' && init?.method === 'DELETE') {
        calls.push('DELETE')
        return new Response(null, { status: 204 })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    const deleteButton = wrapper.findAll('button').find((button) => button.text() === '删除')
    await deleteButton?.trigger('click')
    expect(wrapper.text()).toContain('确认删除（不可恢复）')
    expect(calls).toEqual([])

    const confirm = wrapper.findAll('button').find((button) => button.text().includes('确认删除'))
    await confirm?.trigger('click')
    await flushPromises()

    expect(calls).toEqual(['DELETE'])
    expect(router.currentRoute.value.name).toBe('scenarios')
  })

  it('场景不存在（404）：玩家可读「找不到这个场景」', async () => {
    const { wrapper } = await mountDetail('/scenarios/scn_gone', () =>
      jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: 'scenario not found' }),
    )

    expect(wrapper.find('.error-banner').text()).toContain('找不到这个场景')
  })
})
