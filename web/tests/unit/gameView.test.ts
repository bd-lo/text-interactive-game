import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { GamePlayerViewResponse } from '@/api/types'
import { useGameStore } from '@/stores/game'
import GameView from '@/views/GameView.vue'

const VIEW: GamePlayerViewResponse = {
  game_id: 'g1',
  turn: 3,
  version: 3,
  snapshot_version: 2,
  status: 'active',
  scenario: 'story',
  hp: 18,
  max_hp: 20,
  inventory: [{ item_id: 'item_a', name: '火把', qty: 2 }],
  room: {
    room_id: 'room_entry',
    title: '哨站地窖',
    description: '墙上的信标还暗着。',
    exits: { north: 'room_hall' },
    exit_names: { north: '哨站大厅' },
    items: [{ item_id: 'item_b', name: '古物' }],
    npcs: [{ npc_id: 'npc_a', name: '老哨兵' }],
  },
  goal: {
    title: '余烬哨站',
    description: '找到失踪的哨兵并点亮信标。',
    objectives: [
      { id: 'o1', text: '进入哨站', done: true },
      { id: 'o2', text: '找到老哨兵', done: false },
    ],
  },
  ending_text: null,
  suggested_actions: [],
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/game/:id', name: 'game', component: GameView },
      { path: '/game/:id/replay', name: 'replay', component: { template: '<div>replay</div>' } },
    ],
  })
}

async function mountGame(
  patch: Record<string, unknown> = {},
  props: { view?: GamePlayerViewResponse } = {},
) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useGameStore()
  store.$patch((state) => {
    Object.assign(
      state,
      {
        gameId: 'g1',
        turn: 3,
        version: 3,
        snapshotVersion: 2,
        status: 'active',
        scenario: 'story',
        view: props.view === undefined ? VIEW : props.view,
      },
      patch,
    )
  })
  const router = makeRouter()
  await router.push('/game/g1')
  await router.isReady()
  const wrapper = mount(GameView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, store }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('GameView（玩家视角：目标/建议/结局/诊断折叠）', () => {
  it('目标面板、可点建议、叙事来源徽章与玩家化标题', async () => {
    const { wrapper } = await mountGame()

    const text = wrapper.text()
    expect(text).toContain('哨站地窖')
    expect(text).toContain('余烬哨站')
    expect(text).toContain('找到老哨兵')
    expect(text).toContain('进度 1 / 2')
    expect(text).toContain('离线模板') // narrativeSource 为空时按离线模板展示（2B 契约）
    const chips = wrapper.findAll('button.chip').map((chip) => chip.text())
    expect(chips).toContain('向北走')
    expect(chips).toContain('拿起古物')
    expect(chips).toContain('对老哨兵问好')
  })

  it('开发信息默认折叠在「诊断信息」里，不打扰玩家', async () => {
    const { wrapper } = await mountGame()
    const diagnostics = wrapper.find('.diagnostics')
    expect(diagnostics.exists()).toBe(true)
    expect(diagnostics.attributes('open')).toBeUndefined()
    expect(wrapper.find('.game-head').text()).not.toContain('version')
    expect(diagnostics.text()).toContain('version')
  })

  it('结局：显示结局横幅并禁止继续输入', async () => {
    const endedView: GamePlayerViewResponse = {
      ...VIEW,
      status: 'ended',
      ending_text: '信标在风里亮起，余烬哨站重新有了光。',
    }
    const { wrapper } = await mountGame({ status: 'ended' }, { view: endedView })

    expect(wrapper.text()).toContain('故事结束')
    expect(wrapper.text()).toContain('信标在风里亮起')
    expect(wrapper.find('textarea').attributes('readonly')).toBeDefined()
    expect(wrapper.text()).toContain('开始新的冒险')
  })

  it('新手引导：首个回合前给出「从这里开始」与可点动作', async () => {
    const { wrapper } = await mountGame({ history: [] })
    expect(wrapper.text()).toContain('从这里开始')
    expect(wrapper.findAll('button.chip').length).toBeGreaterThan(0)
  })

  it('错误横幅：玩家可读标题可显示，技术细节折叠', async () => {
    const { wrapper } = await mountGame({
      errorTitle: '这句指令没听懂',
      error: '这个动作没有生效，你停下了脚步。',
      errorDetail: 'INVALID_INPUT：这个动作没有生效',
      errorCode: 'INVALID_INPUT',
    })

    const banner = wrapper.find('.error-banner')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('这句指令没听懂')
    expect(banner.text()).toContain('这个动作没有生效')
    expect(banner.find('.error-details').attributes('open')).toBeUndefined()
  })
})
