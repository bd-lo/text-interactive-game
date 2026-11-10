import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { GamePlayerViewResponse } from '@/api/types'
import { useGameStore } from '@/stores/game'
import GameView from '@/views/GameView.vue'

// P2-1：生产构建 `import.meta.env.DEV === false` → 玩家页面不渲染诊断信息。
// 用 mock 覆盖构建期开关（dev 分支由 gameView.test.ts 覆盖）。
vi.mock('@/utils/env', () => ({ IS_DEV: false }))

const VIEW: GamePlayerViewResponse = {
  game_id: 'g1',
  turn: 3,
  version: 3,
  snapshot_version: 2,
  status: 'active',
  scenario: 'story',
  hp: 18,
  max_hp: 20,
  inventory: [{ item_id: 'item_a', name: '火把', qty: 1 }],
  room: {
    room_id: 'room_entry',
    title: '哨站地窖',
    description: '墙上的信标还暗着。',
    exits: { north: 'room_hall' },
    exit_names: { north: '哨站大厅' },
    items: [],
    npcs: [],
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

async function mountGame(patch: Record<string, unknown> = {}): Promise<ReturnType<typeof mount>> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useGameStore()
  store.$patch((state) => {
    Object.assign(state, { gameId: 'g1', turn: 3, version: 3, status: 'active', view: VIEW }, patch)
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/game/:id', name: 'game', component: GameView },
      { path: '/game/:id/replay', name: 'replay', component: { template: '<div>replay</div>' } },
    ],
  })
  await router.push('/game/g1')
  await router.isReady()
  const wrapper = mount(GameView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

beforeAll(() => {
  localStorage.clear()
})

describe('GameView 生产构建分支（P2-1 诊断信息不入玩家页面）', () => {
  it('生产构建不渲染「诊断信息」面板，也不出现对局 ID/version 调试字段', async () => {
    const wrapper = await mountGame()

    expect(wrapper.find('.diagnostics').exists()).toBe(false)
    const text = wrapper.text()
    expect(text).not.toContain('诊断信息')
    expect(text).not.toContain('对局 ID')
    expect(text).not.toContain('version')
    expect(text).not.toContain('g1') // 对局 ID 不入玩家可见文本
  })

  it('生产构建仍正常渲染玩家界面（状态/目标/冒险记录/只读提示）', async () => {
    const wrapper = await mountGame()

    expect(wrapper.text()).toContain('哨站地窖')
    expect(wrapper.text()).toContain('余烬哨站')
    expect(wrapper.text()).toContain('冒险记录')
    expect(wrapper.text()).toContain('查看/背包等只读指令的正文只在当前会话显示，刷新后不保留（不写入回放）')
    expect(wrapper.text()).toContain('18/20')
  })
})
