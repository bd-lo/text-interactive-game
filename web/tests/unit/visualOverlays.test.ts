import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CrtOverlay from '@/components/CrtOverlay.vue'
import VisualSettings from '@/components/VisualSettings.vue'
import { vReveal, revealAnimationsEnabled } from '@/directives/reveal'
import { useVisualStore } from '@/stores/visual'

function stubMatchMedia(matches: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({
      matches,
      media: '(prefers-reduced-motion: reduce)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete document.documentElement.dataset.visual
  delete document.documentElement.dataset.reducedMotion
})

afterEach(() => {
  useVisualStore().dispose()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
})

describe('CrtOverlay（纯 CSS CRT 层）', () => {
  it('纯装饰：aria-hidden + 四层（扫描线/噪点/暗角/电子束）', () => {
    const wrapper = mount(CrtOverlay)
    expect(wrapper.find('.crt-layer').attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('.crt-scanlines').exists()).toBe(true)
    expect(wrapper.find('.crt-noise').exists()).toBe(true)
    expect(wrapper.find('.crt-vignette').exists()).toBe(true)
    expect(wrapper.find('.crt-beam').exists()).toBe(true)
  })
})

describe('VisualSettings（视觉设置）', () => {
  function mountSettings() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(VisualSettings, { global: { plugins: [pinia] } })
    return { wrapper, visual: useVisualStore() }
  }

  it('顶栏显示当前档位；一键开关可关/开特效', async () => {
    stubMatchMedia(false)
    const { wrapper, visual } = mountSettings()
    visual.init()

    expect(wrapper.find('.visual-toggle').text()).toContain('全特效')

    await wrapper.find('.visual-toggle').trigger('click')
    expect(visual.mode).toBe('off')
    expect(wrapper.find('.visual-toggle').text()).toContain('特效已关')
    expect(wrapper.find('.visual-toggle').attributes('aria-pressed')).toBe('false')

    await wrapper.find('.visual-toggle').trigger('click')
    expect(visual.mode).toBe('full')
  })

  it('三档 radio 切换并持久化（低功耗档）', async () => {
    stubMatchMedia(false)
    const { wrapper, visual } = mountSettings()
    visual.init()

    await wrapper.find('input[value="low"]').setValue(true)

    expect(visual.mode).toBe('low')
    expect(visual.lowPower).toBe(true)
    expect(JSON.parse(String(localStorage.getItem('textgame:visual-settings')))).toEqual({
      mode: 'low',
    })
  })

  it('系统减少动态效果时给出说明（不误导玩家）', () => {
    const { wrapper, visual } = mountSettings()
    visual.$patch({ systemReducedMotion: true })

    expect(wrapper.find('.visual-note').text()).toContain('减少动态效果')
  })
})

describe('v-reveal 指令（滚动进场降级链）', () => {
  it('无 IntersectionObserver 的环境直接可见（绝不隐藏内容）', () => {
    delete document.documentElement.dataset.visual
    delete document.documentElement.dataset.reducedMotion
    const host = { template: '<div v-reveal class="probe">内容</div>' }

    const wrapper = mount(host, { global: { directives: { reveal: vReveal } } })

    expect(wrapper.find('.probe').classes()).toContain('reveal-visible')
    expect(wrapper.find('.probe').text()).toBe('内容')
  })

  it('系统减少动态/关闭特效时直接可见', () => {
    document.documentElement.dataset.reducedMotion = 'true'
    const host = { template: '<div v-reveal class="probe">内容</div>' }
    const wrapper = mount(host, { global: { directives: { reveal: vReveal } } })
    expect(wrapper.find('.probe').classes()).toContain('reveal-visible')

    document.documentElement.dataset.reducedMotion = 'false'
    document.documentElement.dataset.visual = 'off'
    const off = mount(host, { global: { directives: { reveal: vReveal } } })
    expect(off.find('.probe').classes()).toContain('reveal-visible')
  })

  it('有 IntersectionObserver 时进入视口后加 reveal-visible', () => {
    delete document.documentElement.dataset.visual
    delete document.documentElement.dataset.reducedMotion
    const observers: Array<(entries: Array<{ isIntersecting: boolean }>) => void> = []
    const disconnect = vi.fn()
    class FakeObserver {
      constructor(callback: (entries: Array<{ isIntersecting: boolean }>) => void) {
        observers.push(callback)
      }
      observe = vi.fn()
      disconnect = disconnect
      unobserve = vi.fn()
      takeRecords = vi.fn(() => [])
      root = null
      rootMargin = ''
      thresholds = []
    }
    vi.stubGlobal('IntersectionObserver', FakeObserver)

    const host = { template: '<div v-reveal class="probe">内容</div>' }
    const wrapper = mount(host, { global: { directives: { reveal: vReveal } } })
    expect(wrapper.find('.probe').classes()).toContain('reveal')
    expect(wrapper.find('.probe').classes()).not.toContain('reveal-visible')
    expect(revealAnimationsEnabled()).toBe(true)

    observers[0]?.([{ isIntersecting: true }])
    expect(wrapper.find('.probe').classes()).toContain('reveal-visible')
    expect(disconnect).toHaveBeenCalled()
  })
})
