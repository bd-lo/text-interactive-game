/**
 * 041–042 a11y 批次：全局 landmark / 跳过链接 / 焦点工具 / 路由焦点落点。
 *
 * 这些断言把「键盘用户与读屏用户能否进入并穿过页面」钉成回归基线；
 * 不依赖真实浏览器（jsdom 中 focus 对挂载到 document 的节点有效）。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App.vue'
import {
  FOCUSABLE_SELECTOR,
  firstFocusable,
  focusElement,
  focusMainContent,
  isEditableTarget,
} from '@/utils/a11y'

let wrapper: VueWrapper | null = null

beforeEach(() => {
  setActivePinia(createPinia())
  document.body.innerHTML = ''
  document.title = 'TEXTGAME · 文字冒险'
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('offline'))),
  )
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function mountApp(): VueWrapper {
  wrapper = mount(App, {
    global: {
      plugins: [createPinia()],
      stubs: {
        RouterLink: {
          props: ['to'],
          template: '<a :href="String(to)"><slot /></a>',
        },
        RouterView: { template: '<div class="route-stub" />' },
      },
    },
    attachTo: document.body,
  })
  return wrapper
}

describe('a11y 焦点工具（utils/a11y）', () => {
  it('firstFocusable 跳过禁用/隐藏控件，返回 DOM 顺序第一个可聚焦元素', () => {
    document.body.innerHTML = `
      <div id="root">
        <button disabled>禁用</button>
        <input type="hidden" value="x" />
        <a href="#">链接</a>
        <button>按钮</button>
      </div>`
    const root = document.getElementById('root')
    const first = firstFocusable(root)
    expect(first?.tagName).toBe('A')
    expect(first?.textContent).toBe('链接')
    expect(FOCUSABLE_SELECTOR).toContain('button:not([disabled])')
  })

  it('focusElement 对已卸载/空元素静默跳过（不抛错）', () => {
    const detached = document.createElement('button')
    expect(() => focusElement(detached)).not.toThrow()
    expect(() => focusElement(null)).not.toThrow()
  })

  it('isEditableTarget 识别输入控件，避免全局快捷键抢键', () => {
    const input = document.createElement('input')
    const textarea = document.createElement('textarea')
    const select = document.createElement('select')
    const div = document.createElement('div')
    expect(isEditableTarget(input)).toBe(true)
    expect(isEditableTarget(textarea)).toBe(true)
    expect(isEditableTarget(select)).toBe(true)
    expect(isEditableTarget(div)).toBe(false)
    expect(isEditableTarget(null)).toBe(false)
  })

  it('focusMainContent 聚焦 #main-content（跳过链接落点）', async () => {
    const main = document.createElement('main')
    main.id = 'main-content'
    main.tabIndex = -1
    document.body.appendChild(main)

    focusMainContent()
    await nextTick()
    expect(document.activeElement).toBe(main)
  })
})

describe('App 骨架（landmark + 跳过链接）', () => {
  it('首个可 Tab 元素是跳过链接，指向 main#main-content', async () => {
    const app = mountApp()
    await flushPromises()

    const skip = app.find('a.skip-link')
    expect(skip.exists()).toBe(true)
    expect(skip.text()).toBe('跳到主要内容')
    expect(skip.attributes('href')).toBe('#main-content')
    // 跳过链接必须是 DOM 中第一个可聚焦元素（键盘第一站）
    expect(firstFocusable(document.body)).toBe(skip.element)
  })

  it('main / nav / header / footer landmark 齐全，main 可编程聚焦', async () => {
    const app = mountApp()
    await flushPromises()

    const main = app.find('main#main-content')
    expect(main.exists()).toBe(true)
    expect(main.attributes('tabindex')).toBe('-1')
    expect(app.find('nav[aria-label="主导航"]').exists()).toBe(true)
    expect(app.find('header.app-header').exists()).toBe(true)
    expect(app.find('footer.app-footer').exists()).toBe(true)
    expect(app.find('h1').exists()).toBe(true)
  })
})

describe('路由切换后的焦点与标题（SPA 可访问性）', () => {
  it('切换路由后焦点落到 main#main-content，document.title 反映当前页', async () => {
    const main = document.createElement('main')
    main.id = 'main-content'
    main.tabIndex = -1
    document.body.appendChild(main)

    const { default: router } = await import('@/router')
    await router.push('/')
    await router.isReady()
    // 首次加载不抢焦点（跳过链接仍是第一站）
    expect(document.activeElement).not.toBe(main)

    await router.push('/settings')
    await flushPromises()
    expect(document.activeElement).toBe(main)
    expect(document.title).toBe('设置 · TEXTGAME')
  })
})
