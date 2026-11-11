import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ThemeToggle from '@/components/ThemeToggle.vue'
import { THEME_SETTINGS_KEY, useThemeStore } from '@/stores/theme'
import { useVisualStore } from '@/stores/visual'

function seedThemeMeta(): void {
  const meta = document.createElement('meta')
  meta.setAttribute('name', 'theme-color')
  meta.setAttribute('content', '#f6f7fb')
  document.head.appendChild(meta)
}

function mountToggle() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(ThemeToggle, { global: { plugins: [pinia] } })
  return { wrapper, theme: useThemeStore(), visual: useVisualStore() }
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  document.head.innerHTML = ''
  delete document.documentElement.dataset.theme
  document.documentElement.className = ''
  seedThemeMeta()
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
})

afterEach(() => {
  useThemeStore().dispose()
  useVisualStore().dispose()
  delete (document as unknown as { startViewTransition?: unknown }).startViewTransition
  vi.restoreAllMocks()
})

describe('ThemeToggle（顶栏外观切换按钮）', () => {
  it('日间：aria-pressed=true、提示切到夜间、显示太阳图标', () => {
    const { wrapper, theme } = mountToggle()
    theme.setMode('light')

    const button = wrapper.find('.theme-toggle')
    expect(button.attributes('aria-pressed')).toBe('true')
    expect(button.attributes('title')).toContain('日间')
    expect(button.text()).toContain('外观')
    expect(button.text()).toContain('切换日间/夜间')
    expect(wrapper.find('.theme-toggle-svg circle').exists()).toBe(true)
  })

  it('点击一次切到夜间并持久化，再点一次回到日间', async () => {
    const { wrapper, theme } = mountToggle()
    theme.setMode('light')

    await wrapper.find('.theme-toggle').trigger('click')

    expect(theme.resolved).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(JSON.parse(String(localStorage.getItem(THEME_SETTINGS_KEY)))).toEqual({ theme: 'dark' })
    expect(wrapper.find('.theme-toggle').attributes('aria-pressed')).toBe('false')
    expect(wrapper.find('.theme-toggle-svg path').exists()).toBe(true)

    await wrapper.find('.theme-toggle').trigger('click')
    expect(theme.resolved).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('支持 View Transition 时做圆形扩散：写圆心变量、加过渡类、结束后清理', async () => {
    const { wrapper, theme, visual } = mountToggle()
    theme.setMode('light')
    visual.setSystemReducedMotion(false)

    let finish: () => void = () => {}
    const finished = new Promise<void>((resolve) => {
      finish = resolve
    })
    const startViewTransition = vi.fn((update: () => Promise<void> | void) => {
      void update()
      return { finished }
    })
    Object.defineProperty(document, 'startViewTransition', {
      configurable: true,
      value: startViewTransition,
    })

    await wrapper.find('.theme-toggle').trigger('click')

    expect(startViewTransition).toHaveBeenCalledTimes(1)
    expect(document.documentElement.classList.contains('theme-switching')).toBe(true)
    expect(document.documentElement.style.getPropertyValue('--theme-radius')).not.toBe('')

    await flushPromises()
    expect(theme.resolved).toBe('dark')

    finish()
    await flushPromises()
    expect(document.documentElement.classList.contains('theme-switching')).toBe(false)
  })

  it('系统「减少动态效果」时不做扩散动画，但切换立即生效', async () => {
    const { wrapper, theme, visual } = mountToggle()
    theme.setMode('light')
    visual.setSystemReducedMotion(true)
    const startViewTransition = vi.fn()
    Object.defineProperty(document, 'startViewTransition', {
      configurable: true,
      value: startViewTransition,
    })

    await wrapper.find('.theme-toggle').trigger('click')

    expect(startViewTransition).not.toHaveBeenCalled()
    expect(theme.resolved).toBe('dark')
    expect(document.documentElement.classList.contains('theme-switching')).toBe(false)
  })

  it('View Transition 抛错时兜底：仍然完成换肤，不残留过渡类', async () => {
    const { wrapper, theme, visual } = mountToggle()
    theme.setMode('light')
    visual.setSystemReducedMotion(false)
    Object.defineProperty(document, 'startViewTransition', {
      configurable: true,
      value: vi.fn(() => {
        throw new Error('not supported')
      }),
    })

    await wrapper.find('.theme-toggle').trigger('click')

    expect(theme.resolved).toBe('dark')
    expect(document.documentElement.classList.contains('theme-switching')).toBe(false)
  })
})
