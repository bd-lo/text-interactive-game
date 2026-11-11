import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  THEME_COLORS,
  THEME_SETTINGS_KEY,
  isThemeMode,
  loadThemeMode,
  resolveTheme,
  saveThemeMode,
  systemPrefersLight,
  useThemeStore,
} from '@/stores/theme'

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
    media: '(prefers-color-scheme: light)',
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

function seedThemeMeta(): HTMLMetaElement {
  const meta = document.createElement('meta')
  meta.setAttribute('name', 'theme-color')
  meta.setAttribute('content', THEME_COLORS.light)
  document.head.appendChild(meta)
  return meta
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  clearMatchMedia()
  document.head.innerHTML = ''
  delete document.documentElement.dataset.theme
  seedThemeMeta()
})

afterEach(() => {
  useThemeStore().dispose()
  clearMatchMedia()
  vi.restoreAllMocks()
})

describe('外观主题（Aurora Glass：日间 / 夜间 / 跟随系统）', () => {
  it('默认日间；无 matchMedia 的环境按日间解析（与产品默认一致）', () => {
    const theme = useThemeStore()

    expect(theme.mode).toBe('light')
    expect(theme.resolved).toBe('light')
    expect(theme.isLight).toBe(true)
    expect(theme.themeLabel).toBe('日间')
    expect(systemPrefersLight()).toBe(true)
  })

  it('init 把解析结果写到 <html data-theme> 与 meta[theme-color]', () => {
    stubMatchMedia(true)
    const theme = useThemeStore()

    theme.init()

    expect(document.documentElement.dataset.theme).toBe('light')
    expect(
      document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),
    ).toBe(THEME_COLORS.light)
  })

  it('init 读取持久化夜间档并同步浏览器配色', () => {
    localStorage.setItem(THEME_SETTINGS_KEY, JSON.stringify({ theme: 'dark' }))
    stubMatchMedia(true)
    const theme = useThemeStore()

    theme.init()

    expect(theme.mode).toBe('dark')
    expect(theme.isDark).toBe(true)
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(
      document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),
    ).toBe(THEME_COLORS.dark)
  })

  it('setMode 持久化档位并立即换肤（切回日间）', () => {
    stubMatchMedia(true)
    const theme = useThemeStore()
    theme.init()

    theme.setMode('dark')
    expect(JSON.parse(String(localStorage.getItem(THEME_SETTINGS_KEY)))).toEqual({ theme: 'dark' })
    expect(document.documentElement.dataset.theme).toBe('dark')

    theme.setMode('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(theme.themeLabel).toBe('日间')
  })

  it('toggleTheme：日间 ↔ 夜间（把档位固化为显式皮肤）', () => {
    stubMatchMedia(true)
    const theme = useThemeStore()
    theme.init()
    expect(theme.nextThemeLabel).toBe('夜间')

    theme.toggleTheme()
    expect(theme.mode).toBe('dark')
    expect(theme.resolved).toBe('dark')
    expect(theme.nextThemeLabel).toBe('日间')

    theme.toggleTheme()
    expect(theme.mode).toBe('light')
    expect(theme.resolved).toBe('light')
  })

  it('auto 跟随系统偏好，并在系统切换时实时跟随', () => {
    const { listeners } = stubMatchMedia(false)
    const theme = useThemeStore()
    theme.init()

    theme.setMode('auto')
    expect(theme.themeLabel).toBe('跟随系统')
    expect(theme.resolved).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')

    listeners.forEach((listener) => listener({ matches: true }))
    expect(theme.resolved).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('显式档位不受系统偏好影响', () => {
    stubMatchMedia(false)
    const theme = useThemeStore()
    theme.init()

    theme.setMode('light')
    expect(theme.resolved).toBe('light')
    expect(resolveTheme('light', false)).toBe('light')
    expect(resolveTheme('auto', false)).toBe('dark')
  })

  it('持久化容错：损坏/非法档位回退日间', () => {
    expect(loadThemeMode()).toBe('light')

    localStorage.setItem(THEME_SETTINGS_KEY, '{broken json')
    expect(loadThemeMode()).toBe('light')

    localStorage.setItem(THEME_SETTINGS_KEY, JSON.stringify({ theme: 'neon' }))
    expect(loadThemeMode()).toBe('light')

    expect(isThemeMode('auto')).toBe(true)
    expect(isThemeMode('neon')).toBe(false)
    saveThemeMode('dark')
    expect(loadThemeMode()).toBe('dark')
  })

  it('首帧脚本 theme-init.js 与 store 使用同一存储键/配色（防两处漂移）', () => {
    const script = readFileSync(join(process.cwd(), 'public', 'theme-init.js'), 'utf8')

    expect(script).toContain(THEME_SETTINGS_KEY)
    expect(script).toContain(THEME_COLORS.light)
    expect(script).toContain(THEME_COLORS.dark)
  })
})
