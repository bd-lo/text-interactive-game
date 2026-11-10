import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ReplayView from '@/views/ReplayView.vue'

const GAME_ID = 'g1'
/** 玩家可见文本不得出现的内部实体 ID（ULID）形式（AC-23 脱敏，含回放页）。 */
const INTERNAL_ID = /(?:char|npc|item|room|game)_[0-9A-Z]{6,}/

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
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      { path: '/game/:id/replay', name: 'replay', component: ReplayView },
    ],
  })
}

async function mountReplay(handler: (url: string) => Response | Promise<Response>) {
  const mock = vi.fn(async (input: RequestInfo | URL) => handler(String(input)))
  vi.stubGlobal('fetch', mock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/game/g1/replay')
  await router.isReady()
  const wrapper = mount(ReplayView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, mock }
}

beforeEach(() => {
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ReplayView（只读回放页）', () => {
  it('P2-3 回放页常驻一行只读指令提示，标题不露内部 game_ ID，且不影响只读加载', async () => {
    const { wrapper, mock } = await mountReplay(() =>
      jsonResponse(200, { events: [], from_seq: 1, to_seq: 0 }),
    )

    expect(wrapper.text()).toContain('回放')
    expect(wrapper.text()).not.toContain(GAME_ID)
    expect(wrapper.text()).not.toMatch(INTERNAL_ID)
    expect(wrapper.text()).toContain('查看/背包等只读指令的正文不会出现在回放里')
    expect(wrapper.text()).toContain('回放只是重看，不会改变对局')
    expect(mock).toHaveBeenCalledTimes(1)
    expect(String((mock.mock.calls[0] as unknown[])[0])).toContain('/api/v1/games/g1/replay')
  })

  it('回放内容仍按 narrative 渲染；未登记 NPC 的 ULID 不直接展示（「其他角色」）', async () => {
    const event = {
      id: 'evt_000002',
      seq: 2,
      turn_id: 2,
      actor_id: 'npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHKF', // story 场景 NPC：不在固定标签表 → 不得原样展示
      method: 'dialogue',
      event_type: 'DIALOGUE_EXCHANGED',
      params: {},
      result: { narrative: '你走进地窖。' },
      rng_seed: 42,
      ts: '2026-11-10T13:00:00Z',
    }
    const { wrapper } = await mountReplay(() =>
      jsonResponse(200, { events: [event], from_seq: 1, to_seq: 2 }),
    )

    expect(wrapper.text()).toContain('你走进地窖。')
    expect(wrapper.text()).toContain('其他角色')
    expect(wrapper.text()).not.toContain(event.actor_id)
    expect(wrapper.text()).not.toMatch(INTERNAL_ID)
    expect(wrapper.text()).toContain('查看/背包等只读指令的正文不会出现在回放里')
  })
})
