import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import NarrativeHistory from '@/components/NarrativeHistory.vue'
import type { HistoryEntry } from '@/utils/history'

function entries(): HistoryEntry[] {
  return [
    { id: 'turn:2', kind: 'narrative', text: '你捡起了火把。', turnId: 2 },
    { id: 'notice:1', kind: 'notice', text: '（该指令未生效）', turnId: null },
    { id: 'turn:3', kind: 'narrative', text: '你向北走进地窖。', turnId: 3 },
  ]
}

describe('NarrativeHistory（冒险记录日志）', () => {
  it('按顺序渲染全部回合与系统行，系统行有独立样式', () => {
    const wrapper = mount(NarrativeHistory, { props: { entries: entries() } })

    const items = wrapper.findAll('li')
    expect(items).toHaveLength(3)
    expect(items[0]?.text()).toContain('第 2 回合')
    expect(items[0]?.text()).toContain('你捡起了火把。')
    expect(items[1]?.classes()).toContain('history-notice')
    expect(items[1]?.text()).toContain('（该指令未生效）')
    expect(items[2]?.text()).toContain('你向北走进地窖。')
    expect(wrapper.find('.narrative-history').exists()).toBe(true)
  })

  it('加载态与空态：不渲染空白日志', () => {
    const loading = mount(NarrativeHistory, { props: { entries: [], loading: true } })
    expect(loading.text()).toContain('正在载入冒险记录')

    const empty = mount(NarrativeHistory, { props: { entries: [] } })
    expect(empty.text()).toContain('这里会出现你的冒险记录')
    expect(empty.find('.narrative-history').exists()).toBe(false)
  })

  it('P2-2/AC-10d 开场与结局行：独立标签/样式，正文照常渲染', () => {
    const wrapper = mount(NarrativeHistory, {
      props: {
        entries: [
          { id: 'opening', kind: 'opening', text: '墙上的信标还暗着。\n\n目标：余烬哨站（进度 1/2）', turnId: null },
          { id: 'turn:2', kind: 'narrative', text: '你捡起了火把。', turnId: 2 },
          { id: 'ending', kind: 'ending', text: '信标在风里亮起。', turnId: null },
        ],
      },
    })

    const items = wrapper.findAll('li')
    expect(items).toHaveLength(3)
    expect(items[0]?.classes()).toContain('history-opening')
    expect(items[0]?.text()).toContain('开场')
    expect(items[0]?.text()).toContain('墙上的信标还暗着。')
    expect(items[0]?.text()).toContain('目标：余烬哨站（进度 1/2）')
    expect(items[1]?.text()).toContain('第 2 回合')
    expect(items[2]?.classes()).toContain('history-ending')
    expect(items[2]?.text()).toContain('结局')
    expect(items[2]?.text()).toContain('信标在风里亮起。')
  })

  it('P2-3 只读提示：冒险记录页常驻一行「只读指令正文仅当前会话可见/不写入回放」', () => {
    const wrapper = mount(NarrativeHistory, { props: { entries: entries() } })
    expect(wrapper.find('.history-readonly-hint').text()).toContain(
      '查看/背包等只读指令的正文只在当前会话显示，刷新后不保留（不写入回放）',
    )
  })

  it('追加记录后滚动到最新一屏', async () => {
    const wrapper = mount(NarrativeHistory, { props: { entries: entries() } })
    const scroller = wrapper.find('.narrative-history').element as HTMLElement
    Object.defineProperty(scroller, 'scrollHeight', { value: 480, configurable: true })

    await wrapper.setProps({ entries: [...entries(), { id: 'turn:4', kind: 'narrative', text: '你继续前进。', turnId: 4 }] })
    await nextTick()
    await nextTick()

    expect(scroller.scrollTop).toBe(480)
  })
})
