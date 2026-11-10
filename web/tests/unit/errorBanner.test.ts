import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ErrorBanner from '@/components/ErrorBanner.vue'

describe('ErrorBanner（人话标题 + 折叠技术详情 + 手动重试）', () => {
  it('无消息不渲染；有消息渲染标题/正文，技术细节默认收在 details 里', () => {
    const empty = mount(ErrorBanner, { props: { message: '' } })
    expect(empty.find('.error-banner').exists()).toBe(false)

    const wrapper = mount(ErrorBanner, {
      props: {
        title: '这句指令没听懂',
        message: '这个动作没有生效，你停下了脚步。',
        detail: 'INVALID_INPUT：这个动作没有生效',
      },
    })
    const text = wrapper.text()
    expect(text).toContain('这句指令没听懂')
    expect(text).toContain('这个动作没有生效')
    expect(wrapper.find('.error-details').exists()).toBe(true)
    expect(wrapper.find('.error-details').text()).toContain('INVALID_INPUT')
    expect(wrapper.find('.error-details').attributes('open')).toBeUndefined()
  })

  it('retryable 时显示可点击重试按钮（label 可定制）', async () => {
    const wrapper = mount(ErrorBanner, {
      props: { message: '请求超时', retryable: true, retryLabel: '重新发送' },
    })
    const button = wrapper.find('button')
    expect(button.text()).toBe('重新发送')
    await button.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })
})
