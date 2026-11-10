import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TurnFeedback from '@/components/TurnFeedback.vue'
import { collectTurnFeedback, hasTurnFeedback } from '@/utils/feedback'

describe('失败提示分组（effects[].hint + suggested_actions）', () => {
  it('按「提示/可试试」分组；过滤空值、非字符串并去重', () => {
    const feedback = collectTurnFeedback(
      [
        { hint: '这个动作没有被理解。' },
        { hint: '  这个动作没有被理解。  ' }, // 去重
        { hint: '' },
        { hint: 42 },
        {}, // 无 hint
      ],
      ['环顾四周', '环顾四周', '', '查看背包'],
    )

    expect(feedback.hints).toEqual(['这个动作没有被理解。'])
    expect(feedback.actions).toEqual(['环顾四周', '查看背包'])
    expect(hasTurnFeedback(feedback)).toBe(true)
  })

  it('两组都为空时 hasTurnFeedback=false（组件不渲染空白面板）', () => {
    const empty = collectTurnFeedback([], [])
    expect(empty).toEqual({ hints: [], actions: [] })
    expect(hasTurnFeedback(empty)).toBe(false)

    const component = mount(TurnFeedback, { props: { effects: [], suggestedActions: [] } })
    expect(component.find('.turn-feedback').exists()).toBe(false)
  })

  it('组件渲染两组标题与条目（只读展示，不改 text）', () => {
    const wrapper = mount(TurnFeedback, {
      props: {
        effects: [{ hint: '目标不在可达范围内。' }],
        suggestedActions: ['环顾四周', '查看背包'],
      },
    })

    expect(wrapper.text()).toContain('提示')
    expect(wrapper.text()).toContain('目标不在可达范围内。')
    expect(wrapper.text()).toContain('可试试')
    expect(wrapper.text()).toContain('环顾四周')
    expect(wrapper.text()).toContain('查看背包')
    expect(wrapper.find('ul.feedback-actions').exists()).toBe(true)
  })
})
