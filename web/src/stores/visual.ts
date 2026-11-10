/**
 * 视觉特效偏好（H4 炫酷化）：全局「特效开关 / 低功耗」持久化 + 系统 reduced-motion 降级。
 *
 * 口径（H4-BRIEF §2A）：
 * - 所有特效层（Canvas 余烬粒子 / CRT 层 / 打字机 / glitch / 面板动效）只读本 store；
 * - `off` = 关闭全部特效（仅保留静态配色）；`low` = 低功耗（少粒子、低帧率、关 glitch/打字机）；
 * - 尊重系统 `prefers-reduced-motion: reduce`：无法探测的环境（SSR/jsdom）按「减少动态」保守降级，
 *   保证测试与无 matchMedia 环境不跑动画；
 * - 偏好写 `localStorage`，并把 `data-visual` / `data-reduced-motion` 写到 `<html>` 供纯 CSS 降级；
 * - 设置项本身无动画，关闭特效不影响可用性与信息架构。
 */

import { defineStore } from 'pinia'

/** localStorage 键名（结构变更时改版本后缀）。 */
export const VISUAL_SETTINGS_KEY = 'textgame:visual-settings'

/** 特效档位：全特效 / 低功耗 / 关闭。 */
export type VisualMode = 'full' | 'low' | 'off'

/** 合法档位（用于持久化数据校验）。 */
export const VISUAL_MODES: readonly VisualMode[] = ['full', 'low', 'off']

/** 是否为合法特效档位（防手改 localStorage 造成异常状态）。 */
export function isVisualMode(value: unknown): value is VisualMode {
  return typeof value === 'string' && (VISUAL_MODES as readonly string[]).includes(value)
}

/** 读取持久化档位（localStorage 不可用/损坏时回退「全特效」）。 */
export function loadVisualMode(): VisualMode {
  try {
    const raw: unknown = globalThis.localStorage?.getItem(VISUAL_SETTINGS_KEY)
    if (typeof raw === 'string' && raw !== '') {
      const parsed: unknown = JSON.parse(raw)
      if (typeof parsed === 'object' && parsed !== null) {
        const mode = (parsed as { mode?: unknown }).mode
        if (isVisualMode(mode)) {
          return mode
        }
      }
    }
  } catch {
    // 隐私模式/损坏数据：按默认档位
  }
  return 'full'
}

/** 写入持久化档位（失败静默：不阻塞游戏）。 */
export function saveVisualMode(mode: VisualMode): void {
  try {
    globalThis.localStorage?.setItem(VISUAL_SETTINGS_KEY, JSON.stringify({ mode }))
  } catch {
    // 配额满/隐私模式：仅本会话生效
  }
}

/**
 * 系统是否要求减少动态效果。
 *
 * 无 `matchMedia` 的环境（服务端渲染、jsdom 测试）返回 `true`（保守降级）：
 * 无法确认用户偏好时绝不主动播放动画。
 */
export function systemPrefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true
  }
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return true
  }
}

interface VisualState {
  mode: VisualMode
  /** 系统「减少动态效果」偏好（init 时探测 + 监听变化）。 */
  systemReducedMotion: boolean
}

let mediaQuery: MediaQueryList | null = null
let mediaHandler: ((event: MediaQueryListEvent) => void) | null = null

export const useVisualStore = defineStore('visual', {
  state: (): VisualState => ({
    mode: 'full',
    systemReducedMotion: true,
  }),
  getters: {
    /** Canvas 余烬粒子层是否启用（全特效 + 未要求减少动态）。 */
    particlesEnabled(state): boolean {
      return state.mode === 'full' && !state.systemReducedMotion
    },
    /** 低功耗档：粒子减半/降帧、关 glitch/打字机。 */
    lowPower(state): boolean {
      return state.mode === 'low'
    },
    /** CRT 层是否渲染（关闭特效时整层不渲染，降低合成开销）。 */
    crtEnabled(state): boolean {
      return state.mode !== 'off'
    },
    /** CSS 动效（面板进场/页面过渡/reveal）是否启用。 */
    animationsEnabled(state): boolean {
      return state.mode !== 'off' && !state.systemReducedMotion
    },
    /** glitch 标题效果（仅在完整特效下）。 */
    glitchEnabled(state): boolean {
      return state.mode === 'full' && !state.systemReducedMotion
    },
    /** 叙事打字机（仅在完整特效下；可点击跳过）。 */
    typewriterEnabled(state): boolean {
      return state.mode === 'full' && !state.systemReducedMotion
    },
    modeLabel(state): string {
      if (state.mode === 'off') {
        return '特效已关'
      }
      if (state.mode === 'low') {
        return '低功耗'
      }
      return '全特效'
    },
  },
  actions: {
    /** 设置档位并持久化 + 同步 `<html>` 数据属性。 */
    setMode(mode: VisualMode): void {
      this.mode = mode
      saveVisualMode(mode)
      this.applyToDocument()
    },
    /** 顶栏一键开关（开 ↔ 关；低功耗档点一下也回到全特效）。 */
    toggleEffects(): void {
      this.setMode(this.mode === 'off' ? 'full' : 'off')
    },
    setSystemReducedMotion(value: boolean): void {
      this.systemReducedMotion = value
      this.applyToDocument()
    },
    /** 把档位写到 `<html data-visual/data-reduced-motion>`，供纯 CSS 降级。 */
    applyToDocument(): void {
      if (typeof document === 'undefined') {
        return
      }
      const root = document.documentElement
      root.dataset.visual = this.mode
      root.dataset.reducedMotion = this.systemReducedMotion ? 'true' : 'false'
    },
    /** 初始化：读持久化 + 探测系统偏好 + 监听偏好变化（只在应用启动时调用一次）。 */
    init(): void {
      this.mode = loadVisualMode()
      this.systemReducedMotion = systemPrefersReducedMotion()
      this.applyToDocument()
      if (mediaQuery !== null) {
        return
      }
      if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
        try {
          mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
          mediaHandler = (event: MediaQueryListEvent): void => {
            this.setSystemReducedMotion(event.matches)
          }
          mediaQuery.addEventListener?.('change', mediaHandler)
        } catch {
          mediaQuery = null
          mediaHandler = null
        }
      }
    },
    /** 移除系统偏好监听（测试/卸载用）。 */
    dispose(): void {
      if (mediaQuery !== null && mediaHandler !== null) {
        mediaQuery.removeEventListener?.('change', mediaHandler)
      }
      mediaQuery = null
      mediaHandler = null
    },
  },
})
