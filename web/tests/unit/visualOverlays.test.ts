import { enableAutoUnmount, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AtmosphereOverlay from '@/components/AtmosphereOverlay.vue'
import VisualSettings from '@/components/VisualSettings.vue'
import { vReveal, revealAnimationsEnabled } from '@/directives/reveal'
import { useThemeStore } from '@/stores/theme'
import { useVisualStore } from '@/stores/visual'

// 组件在用例结束后必须卸载：否则遗留的 window 监听会污染下一条用例的 rAF 计数。
enableAutoUnmount(afterEach)

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

let rafCallbacks: FrameRequestCallback[] = []

function stubRaf(): void {
  rafCallbacks = []
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafCallbacks.push(callback)
    return rafCallbacks.length
  })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  document.head.innerHTML = ''
  delete document.documentElement.dataset.visual
  delete document.documentElement.dataset.reducedMotion
  delete document.documentElement.dataset.theme
  stubRaf()
})

afterEach(() => {
  useVisualStore().dispose()
  useThemeStore().dispose()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
})

describe('AtmosphereOverlay（极光氛围层）', () => {
  it('纯装饰：aria-hidden + 极光四团 / 聚光 / 颗粒', () => {
    const wrapper = mount(AtmosphereOverlay)
    expect(wrapper.find('.atmosphere-layer').attributes('aria-hidden')).toBe('true')
    expect(wrapper.findAll('.aurora-blob')).toHaveLength(4)
    expect(wrapper.find('.atmosphere-spotlight').exists()).toBe(true)
    expect(wrapper.find('.atmosphere-grain').exists()).toBe(true)
  })

  it('全特效下指针移动会更新聚光坐标（rAF 节流）', () => {
    stubMatchMedia(false)
    const visual = useVisualStore()
    visual.init()
    visual.setMode('full')
    visual.setSystemReducedMotion(false)

    const wrapper = mount(AtmosphereOverlay)
    expect(rafCallbacks).toHaveLength(0)

    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 120, clientY: 80 }))
    expect(rafCallbacks.length).toBe(1)

    rafCallbacks.forEach((callback) => callback(0))
    const layer = wrapper.find('.atmosphere-layer').element as HTMLElement
    expect(layer.style.getPropertyValue('--spot-x')).toBe('120px')
    expect(layer.style.getPropertyValue('--spot-y')).toBe('80px')
  })

  it('非全特效（低功耗/关闭）不更新聚光坐标', () => {
    stubMatchMedia(false)
    const visual = useVisualStore()
    visual.init()
    visual.setMode('low')

    const wrapper = mount(AtmosphereOverlay)
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 12, clientY: 34 }))

    expect(rafCallbacks).toHaveLength(0)
    const layer = wrapper.find('.atmosphere-layer').element as HTMLElement
    expect(layer.style.getPropertyValue('--spot-x')).toBe('')
  })
})

describe('VisualSettings（外观与视觉设置）', () => {
  function mountSettings() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(VisualSettings, { global: { plugins: [pinia] } })
    return { wrapper, visual: useVisualStore(), theme: useThemeStore() }
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

  it('动效三档切换并持久化（低功耗档）', async () => {
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

  it('外观三档切换并持久化（夜间）', async () => {
    stubMatchMedia(false)
    const { wrapper, theme } = mountSettings()

    await wrapper.find('input[value="dark"]').setValue(true)

    expect(theme.resolved).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(JSON.parse(String(localStorage.getItem('textgame:theme-settings')))).toEqual({
      theme: 'dark',
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
