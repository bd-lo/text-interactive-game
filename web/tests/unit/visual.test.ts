import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  VISUAL_SETTINGS_KEY,
  isVisualMode,
  loadVisualMode,
  saveVisualMode,
  useVisualStore,
} from '@/stores/visual'

interface FakeMediaQueryList {
  matches: boolean
  media: string
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
}

/** 安装可控的 `window.matchMedia`（jsdom 默认没有），返回监听器收集数组。 */
function stubMatchMedia(matches: boolean): {
  mql: FakeMediaQueryList
  listeners: Array<(event: { matches: boolean }) => void>
} {
  const listeners: Array<(event: { matches: boolean }) => void> = []
  const mql: FakeMediaQueryList = {
    matches,
    media: '(prefers-reduced-motion: reduce)',
    addEventListener: vi.fn((_type: string, listener: (event: { matches: boolean }) => void) => {
      listeners.push(listener)
    }),
    removeEventListener: vi.fn(),
  }
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => mql) })
  return { mql, listeners }
}

function clearMatchMedia(): void {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  clearMatchMedia()
  delete document.documentElement.dataset.visual
  delete document.documentElement.dataset.reducedMotion
})

afterEach(() => {
  useVisualStore().dispose()
  clearMatchMedia()
  vi.restoreAllMocks()
})

describe('视觉特效偏好（H4 炫酷化开关）', () => {
  it('默认全特效；无 matchMedia 的环境保守降级（不跑粒子/打字机）', () => {
    const visual = useVisualStore()

    expect(visual.mode).toBe('full')
    expect(visual.systemReducedMotion).toBe(true)
    expect(visual.particlesEnabled).toBe(false)
    expect(visual.typewriterEnabled).toBe(false)
    expect(visual.crtEnabled).toBe(true)
  })

  it('init 探测系统偏好并写 <html data-visual/data-reduced-motion>', () => {
    stubMatchMedia(false)
    const visual = useVisualStore()

    visual.init()

    expect(visual.systemReducedMotion).toBe(false)
    expect(visual.particlesEnabled).toBe(true)
    expect(visual.typewriterEnabled).toBe(true)
    expect(document.documentElement.dataset.visual).toBe('full')
    expect(document.documentElement.dataset.reducedMotion).toBe('false')
  })

  it('系统要求减少动态时自动降级（但仍可用纯文本界面）', () => {
    stubMatchMedia(true)
    const visual = useVisualStore()

    visual.init()

    expect(visual.systemReducedMotion).toBe(true)
    expect(visual.particlesEnabled).toBe(false)
    expect(visual.animationsEnabled).toBe(false)
    expect(visual.glitchEnabled).toBe(false)
    expect(document.documentElement.dataset.reducedMotion).toBe('true')
  })

  it('init 读取持久化档位（off）并同步到 <html>', () => {
    localStorage.setItem(VISUAL_SETTINGS_KEY, JSON.stringify({ mode: 'off' }))
    stubMatchMedia(false)
    const visual = useVisualStore()

    visual.init()

    expect(visual.mode).toBe('off')
    expect(visual.crtEnabled).toBe(false)
    expect(visual.particlesEnabled).toBe(false)
    expect(document.documentElement.dataset.visual).toBe('off')
  })

  it('setMode 持久化并切换能力位（low = 低功耗：有 CRT 氛围、无粒子/glitch/打字机）', () => {
    stubMatchMedia(false)
    const visual = useVisualStore()
    visual.init()

    visual.setMode('low')

    expect(visual.mode).toBe('low')
    expect(visual.lowPower).toBe(true)
    expect(visual.crtEnabled).toBe(true)
    expect(visual.particlesEnabled).toBe(false)
    expect(visual.typewriterEnabled).toBe(false)
    expect(JSON.parse(String(localStorage.getItem(VISUAL_SETTINGS_KEY)))).toEqual({ mode: 'low' })
    expect(document.documentElement.dataset.visual).toBe('low')
  })

  it('toggleEffects：全特效 ↔ 关闭特效 一键切换', () => {
    stubMatchMedia(false)
    const visual = useVisualStore()
    visual.init()

    visual.toggleEffects()
    expect(visual.mode).toBe('off')
    expect(visual.modeLabel).toBe('特效已关')

    visual.toggleEffects()
    expect(visual.mode).toBe('full')
  })

  it('监听系统偏好变化（运行中切换「减少动态效果」也降级）', () => {
    const { listeners } = stubMatchMedia(false)
    const visual = useVisualStore()
    visual.init()
    expect(visual.particlesEnabled).toBe(true)

    listeners.forEach((listener) => listener({ matches: true }))

    expect(visual.systemReducedMotion).toBe(true)
    expect(visual.particlesEnabled).toBe(false)
    expect(document.documentElement.dataset.reducedMotion).toBe('true')
  })

  it('持久化数据容错：损坏/非法档位回退 full', () => {
    expect(loadVisualMode()).toBe('full')

    localStorage.setItem(VISUAL_SETTINGS_KEY, '{broken json')
    expect(loadVisualMode()).toBe('full')

    localStorage.setItem(VISUAL_SETTINGS_KEY, JSON.stringify({ mode: 'disco' }))
    expect(loadVisualMode()).toBe('full')

    expect(isVisualMode('low')).toBe(true)
    expect(isVisualMode('disco')).toBe(false)
    saveVisualMode('off')
    expect(loadVisualMode()).toBe('off')
  })
})
