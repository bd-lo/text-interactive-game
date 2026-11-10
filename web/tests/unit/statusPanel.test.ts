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
    inventory: [],
    room: {
      room_id: 'room_entry',
      title: '入口',
      description: '一间石室。',
      exits: { north: 'room_cellar' },
      items: [],
      npcs: [],
    },
  }
}

describe('StatusPanel（服务端数字 + 空/加载/错误态）', () => {
  it('展示服务端 turn/version/snapshot_version（不本地推算）', () => {
    const wrapper = mount(StatusPanel, { props: { view: makeView() } })

    expect(wrapper.text()).toContain('回合数')
    expect(wrapper.text()).toContain('快照版本')
    expect(wrapper.text()).toContain('18/20')

    const values = wrapper.findAll('dd').map((node) => node.text())
    expect(values).toContain('7') // turn
    expect(values).toContain('9') // version
    expect(values).toContain('3') // snapshot_version
    expect(wrapper.text()).toContain('进行中')
  })

  it('加载态：无 view 且 loading=true → 正在加载', () => {
    const wrapper = mount(StatusPanel, { props: { view: null, loading: true } })
    expect(wrapper.text()).toContain('正在加载状态')
  })

  it('错误态：展示只读错误信息', () => {
    const wrapper = mount(StatusPanel, {
      props: { view: null, error: 'NOT_FOUND：存档不存在: x' },
    })
    expect(wrapper.text()).toContain('状态加载失败')
    expect(wrapper.text()).toContain('NOT_FOUND')
  })

  it('空态：未加载且无错误', () => {
    const wrapper = mount(StatusPanel, { props: { view: null } })
    expect(wrapper.text()).toContain('尚无状态数据')
  })
})
