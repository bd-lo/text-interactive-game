import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TurnTextStream from '@/components/TurnTextStream.vue'

describe('TurnTextStream（文本插值渲染，R-L3 定界）', () => {
  it('HTML/脚本一律按文本显示，不产生 DOM 节点', () => {
    const payload = '<script>alert(1)</script><b>bold</b>'
    const wrapper = mount(TurnTextStream, { props: { text: payload } })

    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('b').exists()).toBe(false)
    expect(wrapper.text()).toContain(payload)
  })

  it('按码点显示字数（第 23 章 §23.4 屏字数提示）', () => {
    const wrapper = mount(TurnTextStream, { props: { text: '你好，世界' } })

    expect(wrapper.text()).toContain('5 字')
  })
})
