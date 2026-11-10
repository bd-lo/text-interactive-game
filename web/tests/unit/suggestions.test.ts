import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ActionChips from '@/components/ActionChips.vue'
import TurnFeedback from '@/components/TurnFeedback.vue'
import type { GamePlayerViewResponse } from '@/api/types'
import { collectTurnFeedback, hasTurnFeedback } from '@/utils/feedback'
import {
  exitSuggestions,
  exitTitles,
  isAppAction,
  itemSuggestions,
  npcSuggestions,
  suggestActions,
  suggestCommands,
} from '@/utils/suggestions'

function makeView(overrides: Partial<GamePlayerViewResponse['room']> = {}): GamePlayerViewResponse {
  return {
    game_id: 'g1',
    turn: 2,
    version: 2,
    snapshot_version: 1,
    status: 'active',
    hp: 20,
    max_hp: 20,
    inventory: [],
    room: {
      room_id: 'room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
      title: '入口',
      description: '一间石室。',
      exits: { north: 'room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJM' },
      items: [{ item_id: 'item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ', name: '火把' }],
      npcs: [{ npc_id: 'npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP', name: '老哨兵' }],
      ...overrides,
    },
  }
}

describe('从 /view 派生确定性建议指令', () => {
  it('出口 → 「向北走」；物品 → 「拿起火把」；NPC → 「对老哨兵问好」', () => {
    const view = makeView()
    expect(exitSuggestions(view).map((entry) => entry.command)).toEqual(['向北走'])
    expect(itemSuggestions(view).map((entry) => entry.command)).toEqual(['拿起火把'])
    expect(npcSuggestions(view).map((entry) => entry.command)).toEqual(['对老哨兵问好'])
  })

  it('suggestActions 顺序稳定（出口→物品→NPC→兜底）且 null 视图回退基础动作', () => {
    const view = makeView()
    expect(suggestActions(view)).toEqual(['向北走', '拿起火把', '对老哨兵问好', '环顾四周', '查看背包'])
    expect(suggestActions(null)).toEqual(['环顾四周', '查看背包'])
  })

  it('去重 + 上限 5 条；未知方位回退「去<方向>」不吞未知值', () => {
    const view = makeView({
      exits: { north: 'r1', portal: 'r2' },
      items: [],
      npcs: [],
    })
    expect(suggestCommands(view, 3).map((entry) => entry.command)).toEqual(['向北走', '去portal', '环顾四周'])
  })

  it('应用动作与游戏指令区分（「刷新对局视图」不能当指令提交）', () => {
    expect(isAppAction('刷新对局视图')).toBe(true)
    expect(isAppAction('查看背包')).toBe(false)
  })

  it('exitTitles：用 exit_names 给出「通往哪里」，不泄露房间 ID', () => {
    const view = makeView({ exit_names: { north: '哨站地窖' } })
    expect(exitTitles(view)).toEqual({ 向北走: '通往哨站地窖' })
    expect(exitTitles(null)).toEqual({})
  })
})

describe('ActionChips（可点击指令）', () => {
  it('渲染按钮并 emit command；空列表不渲染容器', async () => {
    const wrapper = mount(ActionChips, { props: { actions: ['向北走', '查看背包'] } })
    const buttons = wrapper.findAll('button.chip')
    expect(buttons.map((button) => button.text())).toEqual(['向北走', '查看背包'])

    await buttons[0]?.trigger('click')
    expect(wrapper.emitted('command')?.[0]).toEqual(['向北走'])

    const empty = mount(ActionChips, { props: { actions: [] } })
    expect(empty.find('.action-chips').exists()).toBe(false)
  })
})

describe('失败提示分组（effects[].hint + suggested_actions）', () => {
  it('按「提示/可以试试」分组；过滤空值、非字符串并去重', () => {
    const feedback = collectTurnFeedback(
      [
        { hint: '这个动作没有被理解。' },
        { hint: '  这个动作没有被理解。  ' },
        { hint: '' },
        { hint: 42 },
        {},
      ],
      ['环顾四周', '环顾四周', '', '查看背包'],
    )

    expect(feedback.hints).toEqual(['这个动作没有被理解。'])
    expect(feedback.actions).toEqual(['环顾四周', '查看背包'])
    expect(hasTurnFeedback(feedback)).toBe(true)
  })

  it('两组都为空时组件不渲染空白面板', () => {
    const empty = collectTurnFeedback([], [])
    expect(hasTurnFeedback(empty)).toBe(false)

    const component = mount(TurnFeedback, { props: { effects: [], suggestedActions: [] } })
    expect(component.find('.turn-feedback').exists()).toBe(false)
  })

  it('提示可读、建议可点击提交', async () => {
    const wrapper = mount(TurnFeedback, {
      props: {
        effects: [{ hint: '目标不在可达范围内。' }],
        suggestedActions: ['环顾四周', '查看背包'],
      },
    })

    expect(wrapper.text()).toContain('提示')
    expect(wrapper.text()).toContain('目标不在可达范围内。')
    expect(wrapper.text()).toContain('可以试试')

    const chips = wrapper.findAll('button.chip')
    expect(chips.map((chip) => chip.text())).toEqual(['环顾四周', '查看背包'])
    await chips[0]?.trigger('click')
    expect(wrapper.emitted('command')?.[0]).toEqual(['环顾四周'])
  })
})
