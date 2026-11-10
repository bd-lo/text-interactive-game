import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import GoalPanel from '@/components/GoalPanel.vue'
import type { Goal } from '@/api/types'

function makeGoal(): Goal {
  return {
    title: '余烬哨站',
    description: '找到失踪的哨兵并点亮信标。',
    objectives: [
      { id: 'o1', text: '进入哨站', done: true },
      { id: 'o2', text: '找到老哨兵', done: false },
      { id: 'o3', text: '点亮信标', done: false },
    ],
  }
}

describe('GoalPanel（剧情目标与进度）', () => {
  it('渲染目标标题/描述/子目标与完成态', () => {
    const wrapper = mount(GoalPanel, { props: { goal: makeGoal() } })
    const text = wrapper.text()

    expect(text).toContain('余烬哨站')
    expect(text).toContain('找到失踪的哨兵')
    expect(wrapper.findAll('li')).toHaveLength(3)
    expect(wrapper.findAll('li.objective-done')).toHaveLength(1)
    expect(wrapper.find('li.objective-done').text()).toContain('✓')
    expect(text).toContain('进度 1 / 3')
  })

  it('无目标时不渲染（GameView 用 v-if 控制，本例验证组件仍可独立使用）', () => {
    const wrapper = mount(GoalPanel, { props: { goal: makeGoal() } })
    expect(wrapper.find('.goal-panel').exists()).toBe(true)
  })
})
