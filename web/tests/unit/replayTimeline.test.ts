import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReplayEventPanel from '@/components/ReplayEventPanel.vue'
import ReplayTimeline from '@/components/ReplayTimeline.vue'
import { NARRATIVE_FALLBACK } from '@/utils/replay'
import type { ReplayEvent } from '@/api/types'

function makeEvent(overrides: Partial<ReplayEvent> = {}): ReplayEvent {
  return {
    id: 'evt_000002',
    seq: 2,
    turn_id: 2,
    actor_id: 'player',
    method: 'take_item',
    event_type: 'ITEM_TAKEN',
    params: { api_key: 'sk-secret-value' },
    result: { narrative: '你拿起火把。' },
    rng_seed: 42,
    ts: '2026-11-10T13:00:00Z',
    ...overrides,
  }
}

function mountTimeline(index: number, count = 5): ReturnType<typeof mount> {
  return mount(ReplayTimeline, {
    props: { count, selectedIndex: index, selectedSeq: 3, lastSeq: 9 },
  })
}

describe('ReplayTimeline（首/末/上一步/下一步 + 拖动 clamp）', () => {
  it('四个按钮发出 clamp 后的目标下标', async () => {
    const wrapper = mountTimeline(2)
    const buttons = wrapper.findAll('button')

    await buttons[0]?.trigger('click') // 首
    await buttons[1]?.trigger('click') // 上一步
    await buttons[2]?.trigger('click') // 下一步
    await buttons[3]?.trigger('click') // 末

    expect(wrapper.emitted('select')).toEqual([[0], [1], [3], [4]])
  })

  it('拖动/点击 range 越界 clamp（99→末，-3→首）', async () => {
    const wrapper = mountTimeline(2)
    const range = wrapper.find('input[type="range"]')

    ;(range.element as HTMLInputElement).value = '99'
    await range.trigger('input')
    ;(range.element as HTMLInputElement).value = '-3'
    await range.trigger('input')

    expect(wrapper.emitted('select')).toEqual([[4], [0]])
  })

  it('无事件时按钮禁用且不渲染 range', () => {
    const wrapper = mountTimeline(-1, 0)

    expect(wrapper.find('input[type="range"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂无已加载事件')
    for (const button of wrapper.findAll('button')) {
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
    }
  })
})

describe('ReplayEventPanel（narrative 渲染 + 敏感 params 不展示）', () => {
  it('展示 result.narrative 与 turn_id/event_type，不泄露 params 内容', () => {
    const wrapper = mount(ReplayEventPanel, { props: { event: makeEvent() } })

    expect(wrapper.text()).toContain('你拿起火把。')
    expect(wrapper.text()).toContain('ITEM_TAKEN')
    expect(wrapper.text()).toContain('turn_id')
    expect(wrapper.text()).not.toContain('sk-secret-value')
  })

  it('narrative 缺失显示兜底文案；无选中事件显示占位', () => {
    const fallback = mount(ReplayEventPanel, { props: { event: makeEvent({ result: {} }) } })
    expect(fallback.text()).toContain(NARRATIVE_FALLBACK)

    const empty = mount(ReplayEventPanel, { props: { event: null } })
    expect(empty.text()).toContain('未选中事件')
  })
})
