import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusPanel from '@/components/StatusPanel.vue'
import type { GamePlayerViewResponse } from '@/api/types'

function makeView(): GamePlayerViewResponse {
  return {
    game_id: 'g1',
    turn: 7,
    version: 9,
    snapshot_version: 3,
    status: 'active',
    hp: 18,
    max_hp: 20,
    inventory: [
      { item_id: 'item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ', name: '火把', qty: 1 },
      { item_id: 'item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJR', name: '治疗药水', qty: 2 },
    ],
    room: {
      room_id: 'room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
      title: '入口',
      description: '一间石室。',
      exits: { north: 'room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJM' },
      items: [{ item_id: 'item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ', name: '火把' }],
      npcs: [{ npc_id: 'npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP', name: '老哨兵' }],
    },
  }
}

/** 面板里允许出现的玩家可读文本，绝不允许内部 ULID。 */
const INTERNAL_ID = /(room|item|npc|char|game)_[0-9A-Z]{6,}/

describe('StatusPanel（玩家化状态面板：服务端数字 + 无内部 ID + 可点击动作）', () => {
  it('展示回合/HP/状态，且不再向玩家暴露版本链数字', () => {
    const wrapper = mount(StatusPanel, { props: { view: makeView() } })
    const text = wrapper.text()

    expect(text).toContain('第 7 回合')
    expect(text).toContain('18/20')
    expect(text).toContain('进行中')
    // 版本/快照版本属于诊断信息（GameView 折叠区），不在玩家状态面板默认展示
    expect(text).not.toContain('快照版本')
    expect(text).not.toContain('版本')
  })

  it('隐藏所有内部 ID（room_/item_/npc_ ULID）', () => {
    const wrapper = mount(StatusPanel, { props: { view: makeView() } })
    expect(wrapper.text()).not.toMatch(INTERNAL_ID)
  })

  it('行囊显示聚合数量 qty（×2）', () => {
    const wrapper = mount(StatusPanel, { props: { view: makeView() } })
    expect(wrapper.text()).toContain('治疗药水 ×2')
    expect(wrapper.text()).toContain('火把 ×1')
  })

  it('出口/物品/NPC 渲染成可点击指令，点击 emit command', async () => {
    const wrapper = mount(StatusPanel, { props: { view: makeView() } })
    const buttons = wrapper.findAll('button.chip')
    const labels = buttons.map((button) => button.text())
    expect(labels).toContain('向北走')
    expect(labels).toContain('拿起火把')
    expect(labels).toContain('对老哨兵问好')

    const north = buttons.find((button) => button.text() === '向北走')
    await north?.trigger('click')
    expect(wrapper.emitted('command')?.[0]).toEqual(['向北走'])
  })

  it('加载态/空态沿用原口径（无 view 不渲染空白面板）', () => {
    const loading = mount(StatusPanel, { props: { view: null, loading: true } })
    expect(loading.text()).toContain('正在加载状态')

    const empty = mount(StatusPanel, { props: { view: null } })
    expect(empty.text()).toContain('对局状态尚未载入')
  })
})
