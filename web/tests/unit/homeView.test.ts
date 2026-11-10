import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import HomeView from '@/views/HomeView.vue'

const SAVE_LIST = {
  games: [
    {
      game_id: 'g1',
      turn: 3,
      version: 3,
      snapshot_version: 2,
      status: 'active',
      scenario: 'story',
      last_text: '你走进地窖，墙上的信标还暗着。',
      updated_at: '2026-11-10T11:52:00Z',
    },
  ],
  total: 1,
}

const CREATED = { game_id: 'g2', turn: 1, version: 1, snapshot_version: 1, save_seed: 'save-42' }

const PLAYER_VIEW = {
  game_id: 'g2',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  status: 'active',
  scenario: 'story',
  hp: 20,
  max_hp: 20,
  inventory: [],
  room: { room_id: 'entry', title: '入口', description: '一间石室。', exits: {}, items: [], npcs: [] },
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

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      { path: '/cards', name: 'cards', component: { template: '<div>cards</div>' } },
    ],
  })
}

async function mountHome(
  handler: (url: string, init?: RequestInit) => Response | Promise<Response>,
) {
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
  return { wrapper, router, mock }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('HomeView（存档列表 / 一键继续 / 新手可读的新建对局）', () => {
  it('渲染服务端存档列表并一键继续（按最近更新倒序来源）', async () => {
    const { wrapper, router } = await mountHome((url) => {
      if (url.startsWith('/api/v1/games?')) return jsonResponse(200, SAVE_LIST)
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    const text = wrapper.text()
    expect(text).toContain('余烬哨站（剧情）')
    expect(text).toContain('第 3 回合')
    expect(text).toContain('你走进地窖')

    const continueButton = wrapper.findAll('button').find((button) => button.text() === '继续')
    expect(continueButton).toBeDefined()
    await continueButton?.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('game')
    expect(router.currentRoute.value.params.id).toBe('g1')
  })

  it('列表读取失败时退回本机最近对局，不影响新建对局入口', async () => {
    localStorage.setItem(
      'textgame:recent-games',
      JSON.stringify([
        {
          gameId: 'g9',
          scenario: 'story',
          turn: 5,
          version: 5,
          lastText: '信标亮了。',
          savedAt: '2026-11-10T11:52:00Z',
          seed: 42,
        },
      ]),
    )
    const { wrapper } = await mountHome(() =>
      jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' }),
    )

    expect(wrapper.text()).toContain('暂时读不到存档列表')
    expect(wrapper.text()).toContain('余烬哨站（剧情）')
    expect(wrapper.text()).toContain('开始新对局')
  })

  it('新建对局：默认剧情场景、种子留空不发送 seed，成功后跳转对局页', async () => {
    const bodies: unknown[] = []
    const { wrapper, router } = await mountHome((url, init) => {
      if (url === '/api/v1/games' && init?.method === 'POST') {
        bodies.push(JSON.parse(String(init.body)))
        return jsonResponse(201, CREATED)
      }
      if (url === '/api/v1/games/g2/view') return jsonResponse(200, PLAYER_VIEW)
      if (url.startsWith('/api/v1/games?')) return jsonResponse(200, { games: [], total: 0 })
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    const startForm = wrapper.find('form.home-form')
    await startForm.trigger('submit')
    await flushPromises()

    expect(bodies).toEqual([{ scenario: 'story' }])
    expect(router.currentRoute.value.name).toBe('game')
    expect(router.currentRoute.value.params.id).toBe('g2')
  })

  it('种子清空/非法时给玩家可读提示，不发建局请求', async () => {
    const { wrapper, mock } = await mountHome((url) => {
      if (url.startsWith('/api/v1/games?')) return jsonResponse(200, { games: [], total: 0 })
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    await wrapper.find('input[type="number"]').setValue('-3')
    await wrapper.find('form.home-form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('随机种子不对')
    const postCalls = mock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined
      return init?.method === 'POST'
    })
    expect(postCalls).toHaveLength(0)
  })
})
