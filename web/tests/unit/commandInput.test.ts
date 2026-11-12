import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CommandInput from '@/components/CommandInput.vue'

const attached: VueWrapper[] = []

/** 焦点断言需要真实挂到 document 上（jsdom 对游离节点不更新 activeElement）。 */
function mountAttached(props: { disabled?: boolean; lastCommand?: string } = {}): VueWrapper {
  const wrapper = mount(CommandInput, {
    props: { disabled: false, ...props },
    attachTo: document.body,
  })
  attached.push(wrapper)
  return wrapper
}

async function type(wrapper: VueWrapper, value: string): Promise<void> {
  await wrapper.find('textarea').setValue(value)
}

afterEach(() => {
  for (const wrapper of attached.splice(0)) {
    wrapper.unmount()
  }
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

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

describe('CommandInput（041 a11y：完整键盘操作）', () => {
  it('全局 `/` 聚焦指令框（非输入控件时）；aria-keyshortcuts 标注可用键', async () => {
    const wrapper = mountAttached()
    await nextTick()
    const textarea = wrapper.find('textarea')
    ;(document.activeElement as HTMLElement | null)?.blur()
    expect(document.activeElement).not.toBe(textarea.element)

    expect(textarea.attributes('aria-keyshortcuts')).toContain('Enter')
    expect(textarea.attributes('aria-keyshortcuts')).toContain('Escape')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }))
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(textarea.element)
  })

  it('Ctrl+Enter / Cmd+Enter 换行不提交（与 Shift+Enter 同口径）', async () => {
    const wrapper = mountAttached()
    await type(wrapper, '第一行')
    const textarea = wrapper.find('textarea')

    await textarea.trigger('keydown', { key: 'Enter', ctrlKey: true })
    await textarea.trigger('keydown', { key: 'Enter', metaKey: true })
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('↑/↓ 翻阅已发送历史，回到末尾清空草稿', async () => {
    const wrapper = mountAttached()
    const textarea = wrapper.find('textarea')

    await type(wrapper, '向东走')
    await textarea.trigger('keydown', { key: 'Enter' })
    await type(wrapper, '拿起火把')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toHaveLength(2)

    await textarea.trigger('keydown', { key: 'ArrowUp' })
    expect((textarea.element as HTMLTextAreaElement).value).toBe('拿起火把')
    await textarea.trigger('keydown', { key: 'ArrowUp' })
    expect((textarea.element as HTMLTextAreaElement).value).toBe('向东走')
    await textarea.trigger('keydown', { key: 'ArrowDown' })
    expect((textarea.element as HTMLTextAreaElement).value).toBe('拿起火把')
    await textarea.trigger('keydown', { key: 'ArrowDown' })
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
  })

  it('Esc 取消当前草稿（清空但保持焦点），不提交', async () => {
    const wrapper = mountAttached()
    await type(wrapper, '写到一半的指令')
    const textarea = wrapper.find('textarea')
    ;(textarea.element as HTMLTextAreaElement).focus()

    await textarea.trigger('keydown', { key: 'Escape' })

    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.emitted('submit')).toBeUndefined()
    expect(document.activeElement).toBe(textarea.element)
  })

  it('提交按钮禁用态语义：空输入禁用、忙碌 aria-busy=true', async () => {
    const wrapper = mountAttached()
    const form = wrapper.find('form.command-input')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(form.attributes('aria-busy')).toBe('false')

    await type(wrapper, '环顾四周')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()

    await wrapper.setProps({ disabled: true })
    expect(wrapper.find('form.command-input').attributes('aria-busy')).toBe('true')
    // 忙碌中仍保留键盘焦点（readonly 不丢焦点）
    expect(wrapper.find('textarea').attributes('readonly')).toBeDefined()
  })
})
