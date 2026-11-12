/**
 * 041–042 a11y 批次：双主题对比度（WCAG 2.1 AA）+ prefers-reduced-motion + live region 语义。
 *
 * 对比度断言直接读 `web/src/styles/theme.css` 的 token 计算（与 docs/design/a11y-checklist.md
 * 的数字同源）：正文 ≥4.5:1、按钮文字 ≥4.5:1、UI 边界与焦点环 ≥3:1（1.4.11 非文本对比）。
 * token 被改回低对比时这里会先红，防止「视觉回归」悄悄破坏可访问性。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ActionChips from '@/components/ActionChips.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import NarrativeHistory from '@/components/NarrativeHistory.vue'
import TurnTextStream from '@/components/TurnTextStream.vue'
import TypewriterText from '@/components/TypewriterText.vue'
import { useVisualStore } from '@/stores/visual'

const CSS = readFileSync(join(process.cwd(), 'src', 'styles', 'theme.css'), 'utf8')

type RGBA = [number, number, number, number]

function cssBlock(marker: string): string {
  const start = CSS.indexOf(marker)
  expect(start, `theme.css 缺少 ${marker}`).toBeGreaterThanOrEqual(0)
  const end = CSS.indexOf('}', start)
  return CSS.slice(start, end)
}

function tokens(block: string): Map<string, string> {
  const map = new Map<string, string>()
  for (const match of block.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
    map.set(match[1] ?? '', (match[2] ?? '').trim())
  }
  return map
}

function parseColor(value: string): RGBA {
  const text = value.trim()
  if (text.startsWith('#')) {
    const hex = text.slice(1)
    return [
      Number.parseInt(hex.slice(0, 2), 16),
      Number.parseInt(hex.slice(2, 4), 16),
      Number.parseInt(hex.slice(4, 6), 16),
      1,
    ]
  }
  const match = text.match(/rgba?\(([^)]+)\)/)
  if (match === null) {
    throw new Error(`无法解析颜色: ${text}`)
  }
  const parts = (match[1] ?? '').split(',').map((part) => Number(part.trim()))
  return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 1]
}

function channel(value: number): number {
  const scaled = value / 255
  return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4
}

function luminance([red, green, blue]: RGBA): number {
  return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
}

function contrast(foreground: RGBA, background: RGBA): number {
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

/** 半透明色合成到不透明底色（同浏览器 sRGB 叠加口径）。 */
function blend(foreground: RGBA, background: RGBA): RGBA {
  const alpha = foreground[3]
  return [
    foreground[0] * alpha + background[0] * (1 - alpha),
    foreground[1] * alpha + background[1] * (1 - alpha),
    foreground[2] * alpha + background[2] * (1 - alpha),
    1,
  ]
}

function withAlpha(color: RGBA, alpha: number): RGBA {
  return [color[0], color[1], color[2], alpha]
}

const LIGHT = tokens(cssBlock(':root {'))
const DARK = tokens(cssBlock(":root[data-theme='dark'] {"))

interface SkinContext {
  name: string
  tokens: Map<string, string>
  bg: RGBA
  panel: RGBA
  inputBg: RGBA
}

function skin(name: string, map: Map<string, string>): SkinContext {
  const bg = parseColor(map.get('bg') ?? '#000')
  const panel = blend(parseColor(map.get('panel-strong') ?? '#fff'), bg)
  const inputBg = blend(parseColor(map.get('input-bg') ?? '#fff'), panel)
  return { name, tokens: map, bg, panel, inputBg }
}

const SKINS: SkinContext[] = [skin('日间', LIGHT), skin('夜间', DARK)]

describe('双主题对比度（WCAG 2.1 AA）', () => {
  it.each(SKINS)('$name：正文/次要文字/强调色/危险/警告 ≥ 4.5:1', (context) => {
    const token = (name: string): RGBA => parseColor(context.tokens.get(name) ?? '#000')

    expect(contrast(token('fg'), context.bg)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token('fg-dim'), context.bg)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token('accent'), context.bg)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token('danger'), context.bg)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token('warn'), context.bg)).toBeGreaterThanOrEqual(4.5)
    // 面板上的次要文字（面板是半透明玻璃底，先合成再算）
    expect(contrast(token('fg-dim'), context.panel)).toBeGreaterThanOrEqual(4.5)
  })

  it.each(SKINS)('$name：主按钮文字 ≥ 4.5:1（渐变两端都算）', (context) => {
    const buttonText = parseColor(context.tokens.get('btn-fg') ?? '#000')
    expect(contrast(buttonText, parseColor(context.tokens.get('btn-from') ?? '#000'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(buttonText, parseColor(context.tokens.get('btn-to') ?? '#000'))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(SKINS)('$name：输入框占位文字 ≥ 4.5:1（占位符是可见文本）', (context) => {
    const dim = parseColor(context.tokens.get('fg-dim') ?? '#000')
    const placeholder = blend(withAlpha(dim, 0.85), context.inputBg)
    expect(contrast(placeholder, context.inputBg)).toBeGreaterThanOrEqual(4.5)
  })

  it.each(SKINS)('$name：输入框/按钮边界 ≥ 3:1（1.4.11 非文本对比）', (context) => {
    const border = parseColor(context.tokens.get('border-strong') ?? '#000')
    expect(contrast(blend(border, context.inputBg), context.inputBg)).toBeGreaterThanOrEqual(3)
    expect(contrast(blend(border, context.panel), context.panel)).toBeGreaterThanOrEqual(3)
  })

  it.each(SKINS)('$name：键盘焦点环 ≥ 3:1（2.4.7 可见焦点）', (context) => {
    const ring = context.tokens.get('ring') ?? ''
    const rgba = [...ring.matchAll(/rgba?\(([^)]+)\)/g)]
    expect(rgba.length).toBeGreaterThanOrEqual(1)
    const parts = (rgba[rgba.length - 1]?.[1] ?? '').split(',').map((part) => Number(part.trim()))
    const ringColor: RGBA = [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 1]
    expect(contrast(blend(ringColor, context.bg), context.bg)).toBeGreaterThanOrEqual(3)
    expect(contrast(blend(ringColor, context.panel), context.panel)).toBeGreaterThanOrEqual(3)
  })

  it('041–042 修正项已落盘：边界 token / 焦点环 / 日间世界书 key / 日间太阳图标', () => {
    // 日间 border-strong 0.22 → 0.5、夜间 0.2 → 0.36（旧值 1.60/1.93:1 不达标）
    expect(LIGHT.get('border-strong')).toBe('rgba(20, 22, 43, 0.5)')
    expect(DARK.get('border-strong')).toBe('rgba(255, 255, 255, 0.36)')
    expect(LIGHT.get('ring')).toContain('rgba(79, 70, 229, 0.9)')
    expect(DARK.get('ring')).toContain('rgba(157, 180, 255, 0.9)')

    const worldKey = CSS.slice(CSS.indexOf('.world-key'), CSS.indexOf('.world-key') + 220)
    expect(worldKey).toContain('color: var(--accent)')
    expect(CSS).toContain('#b45309')
    expect(CSS).toContain('#fbbf24')
  })
})

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

let frames: FrameRequestCallback[] = []
let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  frames = []
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.push(callback)
    return frames.length
  })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  stubMatchMedia(false)
})

afterEach(() => {
  useVisualStore().dispose()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
})

describe('prefers-reduced-motion（系统「减少动态效果」）', () => {
  /**
   * 真实链路：GameView 把 `visual.typewriterEnabled` 传给 NarrativeHistory → TypewriterText.enabled。
   * 这里用同样绑定复现「系统偏好变化 → 父层 prop 变化 → 打字机降级」。
   */
  function mountTypewriterWithStore(text: string): VueWrapper {
    const Host = defineComponent({
      setup() {
        const store = useVisualStore()
        return () =>
          h(TypewriterText, {
            text,
            charsPerSecond: 5,
            enabled: store.typewriterEnabled,
          })
      },
    })
    return mount(Host, { global: { plugins: [pinia] } })
  }

  it('视觉 store 在系统偏好下关闭打字机/粒子/聚光/过渡，但保留静态氛围', () => {
    const visual = useVisualStore()
    visual.$patch({ mode: 'full' })

    visual.setSystemReducedMotion(true)
    expect(visual.typewriterEnabled).toBe(false)
    expect(visual.particlesEnabled).toBe(false)
    expect(visual.spotlightEnabled).toBe(false)
    expect(visual.animationsEnabled).toBe(false)
    expect(visual.atmosphereEnabled).toBe(true)

    visual.setSystemReducedMotion(false)
    expect(visual.typewriterEnabled).toBe(true)
    expect(visual.particlesEnabled).toBe(true)
    expect(visual.spotlightEnabled).toBe(true)
    expect(visual.animationsEnabled).toBe(true)
  })

  it('打字机：系统要求减少动态时立即整段显示（仍显示完整文本、无跳过按钮）', async () => {
    const visual = useVisualStore()
    visual.$patch({ mode: 'full', systemReducedMotion: true })
    const wrapper = mountTypewriterWithStore('风从断裂的门楣间穿过。')

    expect(wrapper.find('.typewriter').text()).toBe('风从断裂的门楣间穿过。')
    expect(wrapper.find('.typewriter-skip').exists()).toBe(false)
    expect(frames).toHaveLength(0)
    wrapper.unmount()
  })

  it('打字机：动画中出现系统减少动态偏好 → 立刻补全整段文本', async () => {
    const visual = useVisualStore()
    visual.$patch({ mode: 'full', systemReducedMotion: false })
    const wrapper = mountTypewriterWithStore('你沿着北边的通道动身。')
    await nextTick()
    expect(wrapper.find('.typewriter-skip').exists()).toBe(true)

    visual.setSystemReducedMotion(true)
    await nextTick()

    expect(wrapper.find('.typewriter').text()).toBe('你沿着北边的通道动身。')
    expect(wrapper.find('.typewriter-skip').exists()).toBe(false)
    wrapper.unmount()
  })

  it('theme.css 提供纯 CSS 降级链（极光/聚光/过渡/透明度/强制颜色）', () => {
    expect(CSS).toContain('@media (prefers-reduced-motion: reduce)')
    expect(CSS).toContain("html[data-reduced-motion='true'] .aurora-blob")
    expect(CSS).toContain("html[data-reduced-motion='true'] .atmosphere-spotlight")
    expect(CSS).toContain('@media (prefers-reduced-transparency: reduce)')
    expect(CSS).toContain('@media (forced-colors: active)')
    expect(CSS).toContain('.skip-link:focus-visible')
  })
})

describe('live region 与控件语义（避免重复播报）', () => {
  it('文本流 role=log + aria-live=polite；冒险记录只播报追加行', () => {
    const stream = mount(TurnTextStream, { props: { text: '你点亮了信标。' } })
    expect(stream.find('section').attributes('role')).toBe('log')
    expect(stream.find('section').attributes('aria-live')).toBe('polite')

    const history = mount(NarrativeHistory, {
      props: { entries: [{ id: 'turn:2', kind: 'narrative', text: '你点亮了信标。', turnId: 2 }] },
    })
    const log = history.find('.narrative-history')
    expect(log.attributes('role')).toBe('log')
    expect(log.attributes('aria-live')).toBe('polite')
    expect(log.attributes('aria-relevant')).toBe('additions')
  })

  it('错误横幅 role=alert（断言式播报一次，不重复）', () => {
    const banner = mount(ErrorBanner, { props: { message: '这个动作没有生效。' } })
    expect(banner.find('.error-banner').attributes('role')).toBe('alert')
    expect(banner.find('.error-banner').attributes('aria-live')).toBeUndefined()
  })

  it('动作 chips 是原生按钮（Tab/Enter 可达），分组有可读名称，禁用态语义正确', () => {
    const wrapper = mount(ActionChips, { props: { actions: ['向北走', '拿起火把'] } })
    const group = wrapper.find('.action-chips')
    expect(group.attributes('role')).toBe('group')
    expect(group.attributes('aria-label')).toBe('可选动作')

    for (const button of wrapper.findAll('button.chip')) {
      expect(button.attributes('type')).toBe('button')
      expect(button.attributes('tabindex')).toBeUndefined()
      expect(button.attributes('disabled')).toBeUndefined()
    }

    const disabled = mount(ActionChips, { props: { actions: ['向北走'], disabled: true } })
    expect(disabled.find('button.chip').attributes('disabled')).toBeDefined()
  })
})
