/**
 * N1-031 条件编辑器（ConditionEditor）：谓词切换 / 参数编辑 / 组合子增删 / 清除。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConditionEditor from '@/components/scenario-editor/ConditionEditor.vue'
import type { ScenarioCondition } from '@/utils/scenarioEditor'

function lastEmitted(wrapper: ReturnType<typeof mount>): ScenarioCondition | null {
  const events = wrapper.emitted('update:modelValue') ?? []
  return (events[events.length - 1]?.[0] as ScenarioCondition | null | undefined) ?? null
}

function mountEditor(modelValue: ScenarioCondition | null) {
  return mount(ConditionEditor, { props: { modelValue, label: '测试条件' } })
}

describe('ConditionEditor（条件表单 → JSON）', () => {
  it('展示当前谓词；切换为 v2 谓词会发出该谓词的骨架对象', async () => {
    const wrapper = mountEditor({ in_room: 'hall' })
    const select = wrapper.find('[data-testid="condition-kind-select"]')
    expect((select.element as HTMLSelectElement).value).toBe('in_room')

    await select.setValue('flag')
    expect(lastEmitted(wrapper)).toEqual({ flag: 'my_flag' })

    // 真实父组件会把 emit 结果回填为新的 modelValue；这里模拟该更新后再改参数。
    await wrapper.setProps({ modelValue: lastEmitted(wrapper) })
    await wrapper.find('[data-testid="condition-scalar"]').setValue('keeper_briefed')
    expect(lastEmitted(wrapper)).toEqual({ flag: 'keeper_briefed' })
  })

  it('数值阈值谓词用 number 输入并发出整数', async () => {
    const wrapper = mountEditor({ hp_lte: 6 })
    const input = wrapper.find('[data-testid="condition-number"]')
    expect((input.element as HTMLInputElement).value).toBe('6')

    await input.setValue('3')
    expect(lastEmitted(wrapper)).toEqual({ hp_lte: 3 })
  })

  it('npc_state / has_item_tag_count / item_in_room 的载荷是嵌套对象', async () => {
    const npcState = mountEditor({ npc_state: { npc: 'keeper', state: 'briefed' } })
    await npcState.find('[data-testid="condition-payload-state"]').setValue('angry')
    expect(lastEmitted(npcState)).toEqual({ npc_state: { npc: 'keeper', state: 'angry' } })

    const count = mountEditor({ has_item_tag_count: { tag: 'quest', at_least: 1 } })
    await count.find('[data-testid="condition-payload-at-least"]').setValue('2')
    expect(lastEmitted(count)).toEqual({ has_item_tag_count: { tag: 'quest', at_least: 2 } })

    const itemRoom = mountEditor({ item_in_room: { item: 'flint', room: 'hall' } })
    await itemRoom.find('[data-testid="condition-payload-room"]').setValue('tower')
    expect(lastEmitted(itemRoom)).toEqual({ item_in_room: { item: 'flint', room: 'tower' } })
  })

  it('all 组合子：递归渲染子条件、添加/删除子条件都发出新数组', async () => {
    const wrapper = mountEditor({ all: [{ in_room: 'hall' }] })
    expect(wrapper.findAll('[data-testid="condition-kind-select"]').length).toBeGreaterThanOrEqual(2)

    const addButtons = wrapper.findAll('button').filter((button) => button.text().includes('添加子条件'))
    expect(addButtons.length).toBe(1)
    await addButtons[0]!.trigger('click')
    const added = lastEmitted(wrapper) as { all: ScenarioCondition[] }
    expect(added.all).toHaveLength(2)
    expect(added.all[1]).toEqual({ in_room: 'entry' })

    const deleteButton = wrapper.findAll('button').find((button) => button.text().trim() === '删除')
    expect(deleteButton).toBeDefined()
    await deleteButton!.trigger('click')
    const removed = lastEmitted(wrapper) as { all: ScenarioCondition[] }
    expect(removed.all).toHaveLength(0)
  })

  it('not 组合子递归编辑子条件；always 用恒真/恒假下拉', async () => {
    const notWrapper = mountEditor({ not: { in_room: 'hall' } })
    const scalar = notWrapper.find('[data-testid="condition-scalar"]')
    expect((scalar.element as HTMLInputElement).value).toBe('hall')
    await scalar.setValue('tower')
    expect(lastEmitted(notWrapper)).toEqual({ not: { in_room: 'tower' } })

    const alwaysWrapper = mountEditor({ always: true })
    await alwaysWrapper.find('[data-testid="condition-always"]').setValue('false')
    expect(lastEmitted(alwaysWrapper)).toEqual({ always: false })
  })

  it('选择「无条件 / 清除」发出 null；a11y：条件 JSON 实时可见且选择框有 label', () => {
    const wrapper = mountEditor({ in_room: 'hall' })
    expect(wrapper.find('legend').text()).toContain('测试条件')
    expect(wrapper.find('legend').text()).toContain('in_room')
    expect(wrapper.find('label').text()).toContain('条件类型')

    void wrapper.find('[data-testid="condition-kind-select"]').setValue('')
    expect(lastEmitted(wrapper)).toBeNull()
  })
})
