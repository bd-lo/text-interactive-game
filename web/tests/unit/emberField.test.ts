import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import EmberField from '@/components/EmberField.vue'
import { useVisualStore } from '@/stores/visual'

interface FakeContext2D {
  setTransform: ReturnType<typeof vi.fn>
  clearRect: ReturnType<typeof vi.fn>
  drawImage: ReturnType<typeof vi.fn>
  createRadialGradient: ReturnType<typeof vi.fn>
  fillRect: ReturnType<typeof vi.fn>
  fillStyle: string
  globalAlpha: number
  globalCompositeOperation: string
}

function makeContext(): FakeContext2D {
  return {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    drawImage: vi.fn(),
    createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    fillRect: vi.fn(),
    fillStyle: '',
    globalAlpha: 1,
    globalCompositeOperation: 'source-over',
  }
}

let rafCallbacks: FrameRequestCallback[] = []
let pinia = createPinia()

function stubRaf(): void {
  rafCallbacks = []
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafCallbacks.push(callback)
    return rafCallbacks.length
  })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
}

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

function mountEmber() {
  setActivePinia(pinia)
  return mount(EmberField, { global: { plugins: [pinia] } })
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  stubRaf()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () => makeContext() as unknown as CanvasRenderingContext2D,
  )
})

afterEach(() => {
  useVisualStore().dispose()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
})

describe('EmberField（Canvas2D 余烬/星尘粒子层）', () => {
  it('启用时挂载即启动 rAF 循环，卸载时取消并清理事件监听', () => {
    stubMatchMedia(false)
    useVisualStore().init()
    const removeDocument = vi.spyOn(document, 'removeEventListener')
    const removeWindow = vi.spyOn(window, 'removeEventListener')

    const wrapper = mountEmber()
    expect(rafCallbacks.length).toBe(1)
    expect(HTMLCanvasElement.prototype.getContext).toHaveBeenCalled()

    wrapper.unmount()
    expect(cancelAnimationFrame).toHaveBeenCalled()
    expect(removeDocument).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
    expect(removeWindow).toHaveBeenCalledWith('resize', expect.any(Function))
  })

  it('低功耗档不启动粒子循环（只有氛围层）', () => {
    stubMatchMedia(false)
    const visual = useVisualStore()
    visual.init()
    visual.setMode('low')

    mountEmber()

    expect(rafCallbacks).toHaveLength(0)
  })

  it('系统减少动态效果时不启动粒子循环', () => {
    stubMatchMedia(true)
    useVisualStore().init()

    mountEmber()

    expect(rafCallbacks).toHaveLength(0)
  })

  it('canvas 2d 不可用（如 jsdom）时优雅降级：不崩溃、不启动循环', () => {
    stubMatchMedia(false)
    useVisualStore().init()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => null)

    const wrapper = mountEmber()

    expect(wrapper.find('canvas.ember-field').exists()).toBe(true)
    expect(rafCallbacks).toHaveLength(0)
  })

  it('页面隐藏时暂停、恢复可见时继续（省电）', () => {
    stubMatchMedia(false)
    useVisualStore().init()
    mountEmber()
    expect(rafCallbacks.length).toBe(1)

    let hidden = true
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(cancelAnimationFrame).toHaveBeenCalled()

    const queuedBeforeResume = rafCallbacks.length
    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    expect(rafCallbacks.length).toBeGreaterThan(queuedBeforeResume)
  })
})
