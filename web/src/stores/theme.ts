/**
 * 外观主题（Aurora Glass）：日间 / 夜间 / 跟随系统，全局持久化 + `<html data-theme>` 同步。
 *
 * 口径：
 * - 默认「日间」（存量体验的一次性切换：旧深绿终端皮肤已彻底移除）；
 * - `auto` 跟随系统 `prefers-color-scheme`，运行中系统切换会实时跟随；
 * - 解析结果只写 `light` / `dark` 到 `<html data-theme>`，纯 CSS 无需理解 `auto`；
 * - 同时同步 `<meta name="theme-color">`（移动端地址栏配色）；
 * - 偏好写 `localStorage`，且 `web/public/theme-init.js` 在首帧前读取同一键，避免刷新闪白；
 * - 主题只负责配色，动效强弱仍由 `stores/visual.ts` 决定（两者正交）。
 */

import { defineStore } from 'pinia'

/** localStorage 键名（结构变更时改版本后缀；`web/public/theme-init.js` 必须同步）。 */
export const THEME_SETTINGS_KEY = 'textgame:theme-settings'

/** 外观档位：跟随系统 / 日间 / 夜间。 */
export type ThemeMode = 'auto' | 'light' | 'dark'

/** 解析后的实际皮肤（只可能是这两种，CSS 只认这个）。 */
export type ResolvedTheme = 'light' | 'dark'

/** 合法档位（用于持久化数据校验）。 */
export const THEME_MODES: readonly ThemeMode[] = ['light', 'dark', 'auto']

/** 每一套皮肤对应的浏览器 UI 配色（`meta[name=theme-color]`）。 */
export const THEME_COLORS: Readonly<Record<ResolvedTheme, string>> = {
  light: '#f6f7fb',
  dark: '#070814',
}

/** 是否为合法外观档位（防手改 localStorage 造成异常状态）。 */
export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (THEME_MODES as readonly string[]).includes(value)
}

/** 是否为解析后皮肤。 */
export function isResolvedTheme(value: unknown): value is ResolvedTheme {
  return value === 'light' || value === 'dark'
}

/** 把档位 + 系统偏好解析成实际皮肤（`auto` 时跟随系统）。 */
export function resolveTheme(mode: ThemeMode, prefersLight: boolean): ResolvedTheme {
  if (mode === 'auto') {
    return prefersLight ? 'light' : 'dark'
  }
  return mode
}

/** 读取持久化档位（localStorage 不可用/损坏时回退「日间」）。 */
export function loadThemeMode(): ThemeMode {
  try {
    const raw: unknown = globalThis.localStorage?.getItem(THEME_SETTINGS_KEY)
    if (typeof raw === 'string' && raw !== '') {
      const parsed: unknown = JSON.parse(raw)
      if (typeof parsed === 'object' && parsed !== null) {
        const mode = (parsed as { theme?: unknown }).theme
        if (isThemeMode(mode)) {
          return mode
        }
      }
    }
  } catch {
    // 隐私模式/损坏数据：按默认档位
  }
  return 'light'
}

/** 写入持久化档位（失败静默：不阻塞游戏）。 */
export function saveThemeMode(mode: ThemeMode): void {
  try {
    globalThis.localStorage?.setItem(THEME_SETTINGS_KEY, JSON.stringify({ theme: mode }))
  } catch {
    // 配额满/隐私模式：仅本会话生效
  }
}

/**
 * 系统是否偏好浅色。
 *
 * 无 `matchMedia` 的环境（服务端渲染、jsdom 测试）返回 `true`：
 * 与应用默认皮肤（日间）保持一致，避免「探测不到就变深色」的意外。
 */
export function systemPrefersLight(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true
  }
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches
  } catch {
    return true
  }
}

interface ThemeState {
  mode: ThemeMode
  /** 解析后的实际皮肤（`data-theme` 写入值）。 */
  resolved: ResolvedTheme
  /** 系统偏好浅色（init 时探测 + 监听变化，仅 `auto` 档使用）。 */
  systemPrefersLight: boolean
}

let schemeQuery: MediaQueryList | null = null
let schemeHandler: ((event: MediaQueryListEvent) => void) | null = null

export const useThemeStore = defineStore('theme', {
  state: (): ThemeState => ({
    mode: 'light',
    resolved: 'light',
    systemPrefersLight: true,
  }),
  getters: {
    /** 当前是否为日间皮肤（顶栏按钮 `aria-pressed` 用它）。 */
    isLight(state): boolean {
      return state.resolved === 'light'
    },
    isDark(state): boolean {
      return state.resolved === 'dark'
    },
    /** 档位文案（菜单/提示用）。 */
    themeLabel(state): string {
      if (state.mode === 'auto') {
        return '跟随系统'
      }
      return state.mode === 'light' ? '日间' : '夜间'
    },
    /** 点击顶栏按钮后将切换到哪个皮肤（用于 title 提示）。 */
    nextThemeLabel(state): string {
      return state.resolved === 'light' ? '夜间' : '日间'
    },
  },
  actions: {
    /** 设置档位并持久化 + 同步 `<html>` 与浏览器 UI 配色。 */
    setMode(mode: ThemeMode): void {
      this.mode = mode
      this.resolved = resolveTheme(mode, this.systemPrefersLight)
      saveThemeMode(mode)
      this.applyToDocument()
    },
    /** 顶栏一键切换：日间 ↔ 夜间（会把档位固化为显式皮肤）。 */
    toggleTheme(): void {
      this.setMode(this.resolved === 'light' ? 'dark' : 'light')
    },
    setSystemPrefersLight(value: boolean): void {
      this.systemPrefersLight = value
      this.resolved = resolveTheme(this.mode, value)
      this.applyToDocument()
    },
    /** 把解析后的皮肤写到 `<html data-theme>` + `meta[theme-color]`，供纯 CSS 取色。 */
    applyToDocument(): void {
      if (typeof document === 'undefined') {
        return
      }
      const root = document.documentElement
      root.dataset.theme = this.resolved
      const meta = document.querySelector('meta[name="theme-color"]')
      if (meta !== null) {
        meta.setAttribute('content', THEME_COLORS[this.resolved])
      }
    },
    /** 初始化：读持久化 + 探测系统偏好 + 监听偏好变化（只在应用启动时调用一次）。 */
    init(): void {
      this.mode = loadThemeMode()
      this.systemPrefersLight = systemPrefersLight()
      this.resolved = resolveTheme(this.mode, this.systemPrefersLight)
      this.applyToDocument()
      if (schemeQuery !== null) {
        return
      }
      if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
        try {
          schemeQuery = window.matchMedia('(prefers-color-scheme: light)')
          schemeHandler = (event: MediaQueryListEvent): void => {
            this.setSystemPrefersLight(event.matches)
          }
          schemeQuery.addEventListener?.('change', schemeHandler)
        } catch {
          schemeQuery = null
          schemeHandler = null
        }
      }
    },
    /** 移除系统偏好监听（测试/卸载用）。 */
    dispose(): void {
      if (schemeQuery !== null && schemeHandler !== null) {
        schemeQuery.removeEventListener?.('change', schemeHandler)
      }
      schemeQuery = null
      schemeHandler = null
    },
  },
})
