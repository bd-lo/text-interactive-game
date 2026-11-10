import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TypewriterText from '@/components/TypewriterText.vue'
import { useVisualStore } from '@/stores/visual'

let frameCallbacks: FrameRequestCallback[] = []
let nextId = 1

async function runFrame(timestamp: number): Promise<void> {
  const callbacks = frameCallbacks
  frameCallbacks = []
  for (const callback of callbacks) {
    callback(timestamp)
  }
  await nextTick()
}

beforeEach(() => {
  frameCallbacks = []
  nextId = 1
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frameCallbacks.push(callback)
    return nextId++
  })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  useVisualStore().dispose()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function mountTypewriter(props: {
  text: string
  enabled?: boolean
  charsPerSecond?: number
  skipLabel?: string
}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(TypewriterText, { props, global: { plugins: [pinia] } })
  await nextTick()
  return wrapper
}

describe('TypewriterText（叙事打字机）', () => {
  it('显式关闭时立即显示完整文本，不启动 rAF', async () => {
    const wrapper = await mountTypewriter({ text: '你走进地窖。', enabled: false })

    expect(wrapper.find('.typewriter').text()).toBe('你走进地窖。')
    expect(wrapper.find('.typewriter-skip').exists()).toBe(false)
    expect(frameCallbacks).toHaveLength(0)
  })

  it('无 matchMedia 环境（系统偏好未知）默认不播放动画，直接整段显示', async () => {
    // jsdom 没有 matchMedia → 视觉 store 保守降级
    const wrapper = await mountTypewriter({ text: '风从断裂的门楣间穿过。' })

    expect(wrapper.find('.typewriter').text()).toBe('风从断裂的门楣间穿过。')
  })

  it('启用后逐帧显示，点击跳过立即完成', async () => {
    const wrapper = await mountTypewriter({
      text: '你沿着北边的通道动身，脚底的石板缝隙里积着陈年的灰。',
      enabled: true,
      charsPerSecond: 10,
    })

    expect(wrapper.find('.typewriter-skip').exists()).toBe(true)
    expect(wrapper.find('.typewriter-visible').text()).toBe('')

    await runFrame(0)
    await runFrame(200)
    const partial = wrapper.find('.typewriter-visible').text()
    expect(partial.length).toBeGreaterThan(0)
    expect(partial.length).toBeLessThan(10)

    await wrapper.find('.typewriter-skip').trigger('click')
    expect(wrapper.find('.typewriter').text()).toBe(
      '你沿着北边的通道动身，脚底的石板缝隙里积着陈年的灰。',
    )
    expect(wrapper.find('.typewriter-skip').exists()).toBe(false)
  })

  it('逐帧推进直到完成，完成后不再请求动画帧', async () => {
    const wrapper = await mountTypewriter({
      text: '一二三四五六七八九十',
      enabled: true,
      charsPerSecond: 10,
    })

    let timestamp = 0
    for (let index = 0; index < 40; index += 1) {
      timestamp += 50
      await runFrame(timestamp)
    }

    expect(wrapper.find('.typewriter').text()).toBe('一二三四五六七八九十')
    expect(frameCallbacks).toHaveLength(0)
  })

  it('卸载时取消未完成的动画帧', async () => {
    const wrapper = await mountTypewriter({
      text: '一段还没打完的叙事文本。',
      enabled: true,
      charsPerSecond: 5,
    })
    expect(frameCallbacks.length).toBeGreaterThan(0)

    wrapper.unmount()

    expect(cancelAnimationFrame).toHaveBeenCalled()
  })

  it('读屏/正文不重复：动画中 sr-only 全文 + 视觉层 aria-hidden；结束后只留一份完整文本', async () => {
    const animating = await mountTypewriter({
      text: '灯亮了。',
      enabled: true,
      charsPerSecond: 5,
    })
    expect(animating.find('.sr-only').text()).toBe('灯亮了。')
    expect(animating.find('.typewriter-visible').attributes('aria-hidden')).toBe('true')

    await animating.find('.typewriter-skip').trigger('click')
    expect(animating.find('.sr-only').exists()).toBe(false)
    expect(animating.find('.typewriter-visible').exists()).toBe(false)
    // 完成后正文只出现一次（e2e/复制/读屏都不会看到重复正文）
    const completedText = animating.find('.typewriter').element.textContent ?? ''
    expect(completedText.split('灯亮了。').length - 1).toBe(1)

    const staticOne = await mountTypewriter({ text: '灯亮了。', enabled: false })
    expect(staticOne.find('.typewriter').text()).toBe('灯亮了。')
    expect(staticOne.find('.sr-only').exists()).toBe(false)
  })
})
