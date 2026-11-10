import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CommandInput from '@/components/CommandInput.vue'

async function type(wrapper: ReturnType<typeof mount>, value: string): Promise<void> {
  await wrapper.find('textarea').setValue(value)
}

describe('CommandInput（键盘优先 + IME + 焦点保留）', () => {
  it('Enter 提交并清空；Shift+Enter 换行不提交', async () => {
    const wrapper = mount(CommandInput, { props: { disabled: false } })
    await type(wrapper, '向北走')

    const textarea = wrapper.find('textarea')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')?.[0]).toEqual(['向北走'])
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')

    await type(wrapper, '第二句')
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('IME 组合输入期间（isComposing）不误发', async () => {
    const wrapper = mount(CommandInput, { props: { disabled: false } })
    await type(wrapper, '正在输入')
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter', isComposing: true })
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('↑ 调出上一条指令（输入框为空时）', async () => {
    const wrapper = mount(CommandInput, { props: { disabled: false, lastCommand: '查看背包' } })
    const textarea = wrapper.find('textarea')
    await textarea.trigger('keydown', { key: 'ArrowUp' })
    expect((textarea.element as HTMLTextAreaElement).value).toBe('查看背包')
  })

  it('忙碌时用 readonly 保持焦点（不丢键盘位置），且不提交', async () => {
    const wrapper = mount(CommandInput, { props: { disabled: true } })
    const textarea = wrapper.find('textarea')
    expect(textarea.attributes('readonly')).toBeDefined()
    expect(textarea.attributes('disabled')).toBeUndefined()

    await type(wrapper, '被禁止的指令')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
