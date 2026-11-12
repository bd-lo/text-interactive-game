/**
 * N2-043/044 移动端布局回归：375px 触控目标 / 换行 / 断点收敛（CSS 契约）+ 对局页流式/长文 DOM 钩子。
 *
 * CSS 断言直接读 `web/src/styles/theme.css`：防止后续改样式时把「窄屏才放大」的触控规则、
 * 长文本换行或网格收敛删掉（e2e mobile.375 在真实浏览器做最终验收）。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { GamePlayerViewResponse } from '@/api/types'
import { useGameStore } from '@/stores/game'
import GameView from '@/views/GameView.vue'

const CSS = readFileSync(join(process.cwd(), 'src', 'styles', 'theme.css'), 'utf8')

/** 去掉注释（保留 @media 标记，供断点块提取）。 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** 去掉注释与媒体查询头部，保留规则体（便于顶层规则解析）。 */
function cleanCss(source: string): string {
  return stripComments(source).replace(/@media[^{]*\{/g, '{')
}

interface CssRule {
  selectors: string[]
  body: string
}

/** 提取指定 @media 标记的全部块内容（支持同名断点出现多次）。 */
function mediaBlocks(marker: string): string[] {
  const css = stripComments(CSS)
  const blocks: string[] = []
  let index = css.indexOf(marker)
  while (index >= 0) {
    const open = css.indexOf('{', index)
    let depth = 1
    let cursor = open + 1
    while (cursor < css.length && depth > 0) {
      if (css[cursor] === '{') {
        depth += 1
      } else if (css[cursor] === '}') {
        depth -= 1
      }
      cursor += 1
    }
    blocks.push(css.slice(open + 1, cursor - 1))
    index = css.indexOf(marker, cursor)
  }
  return blocks
}

/** 解析一个 CSS 块里的顶层规则（选择器列表 + 规则体）。 */
function parseRules(block: string): CssRule[] {
  const rules: CssRule[] = []
  let cursor = 0
  while (cursor < block.length) {
    const open = block.indexOf('{', cursor)
    if (open < 0) {
      break
    }
    const selectorText = block.slice(cursor, open).trim()
    let depth = 1
    let end = open + 1
    while (end < block.length && depth > 0) {
      if (block[end] === '{') {
        depth += 1
      } else if (block[end] === '}') {
        depth -= 1
      }
      end += 1
    }
    const body = block.slice(open + 1, end - 1)
    if (selectorText !== '') {
      rules.push({
        selectors: selectorText
          .split(',')
          .map((selector) => selector.trim())
          .filter((selector) => selector !== ''),
        body,
      })
    }
    cursor = end
  }
  return rules
}

function allRules(blocks: string[]): CssRule[] {
  return blocks.flatMap((block) => parseRules(block))
}

function ruleFor(rules: CssRule[], selector: string): CssRule | undefined {
  return rules.find((rule) => rule.selectors.includes(selector))
}

const MOBILE_760 = mediaBlocks('@media (max-width: 760px)')
const MOBILE_480 = mediaBlocks('@media (max-width: 480px)')
const MOBILE_RULES = allRules([...MOBILE_760, ...MOBILE_480])
const BASE_RULES = parseRules(cleanCss(CSS).replace(/@media[^{]*\{/g, '{'))

describe('移动端 375px 触控目标（N2-044）', () => {
  it('窄屏媒体查询存在（760px/480px）且不限死桌面', () => {
    expect(MOBILE_760.length).toBeGreaterThanOrEqual(2)
    expect(MOBILE_480.length).toBeGreaterThanOrEqual(1)
    // 触控放大规则只在 max-width 查询内（1280px 桌面密度不受影响）
    const touchSelectors = ['.btn', '.chip', '.app-nav a', '.theme-toggle']
    for (const selector of touchSelectors) {
      const inMobile = MOBILE_RULES.some((rule) => rule.selectors.includes(selector))
      expect(inMobile, `${selector} 应在窄屏媒体查询内放大`).toBe(true)
    }
  })

  it('按钮/chips/图标按钮/分页等 ≥44×44（min-height/min-width 44）', () => {
    const selectors = [
      '.btn',
      '.btn-small',
      '.chip',
      '.theme-toggle',
      '.visual-toggle',
      '.typewriter-skip',
      '.app-nav a',
      '.app-brand a',
    ]
    for (const selector of selectors) {
      const rule = ruleFor(MOBILE_RULES, selector)
      expect(rule, `${selector} 缺少窄屏触控规则`).toBeDefined()
      expect(rule?.body).toContain('min-height: 44px')
      expect(rule?.body).toContain('min-width: 44px')
    }
  })

  it('输入/下拉/摘要/开关/复选标签也是可点目标（≥44 高）', () => {
    const inputRule = ruleFor(MOBILE_RULES, '.input')
    expect(inputRule?.body).toContain('min-height: 44px')
    expect(ruleFor(MOBILE_RULES, 'summary')?.body).toContain('min-height: 44px')
    expect(ruleFor(MOBILE_RULES, '.editor-checkbox')?.body).toContain('min-height: 44px')
    expect(ruleFor(MOBILE_RULES, '.visual-option')?.body).toContain('min-height: 44px')
    const switchRule = ruleFor(MOBILE_RULES, '.free-craft-switch')
    expect(switchRule?.body).toContain('height: 44px')
    expect(switchRule?.body).toContain('width: 58px')
  })
})

describe('移动端 375px 布局与长文本（N2-043）', () => {
  it('窄屏网格收敛为单列（表单/目标/条件编辑器不撑宽）', () => {
    expect(ruleFor(MOBILE_RULES, '.llm-form-row')?.body).toContain(
      'grid-template-columns: minmax(0, 1fr)',
    )
    expect(ruleFor(MOBILE_RULES, '.condition-row')?.body).toContain(
      'grid-template-columns: minmax(0, 1fr)',
    )
    expect(ruleFor(MOBILE_RULES, '.condition-child')?.body).toContain('flex-wrap: wrap')
    const editorGrid = MOBILE_760.some((block) =>
      block.includes('.editor-grid') && block.includes('grid-template-columns: minmax(0, 1fr)'),
    )
    expect(editorGrid).toBe(true)
  })

  it('长文本容器强制换行（overflow-wrap:anywhere / word-break）', () => {
    const wrapRule = MOBILE_RULES.find(
      (rule) =>
        rule.selectors.includes('.history-text') && rule.body.includes('overflow-wrap: anywhere'),
    )
    expect(wrapRule).toBeDefined()
    for (const selector of [
      '.turn-text-body',
      '.last-response-text',
      '.pre-text',
      '.scenario-desc',
      '.world-entry-content',
      '.llm-status-detail',
    ]) {
      expect(wrapRule?.selectors, `${selector} 应参与窄屏换行规则`).toContain(selector)
    }
    // 标题/卡片名同样在窄屏允许断行
    const headingWrap = MOBILE_RULES.some(
      (rule) => rule.selectors.includes('.app-main h2') && rule.body.includes('overflow-wrap'),
    )
    expect(headingWrap).toBe(true)
  })

  it('长 JSON/长列表用滚动容器（不撑破视口）', () => {
    expect(ruleFor(BASE_RULES, '.scenario-raw-json')?.body).toContain('overflow: auto')
    expect(ruleFor(BASE_RULES, '.narrative-history')?.body).toContain('overflow-y: auto')
    expect(ruleFor(BASE_RULES, '.world-lore-entries')?.body).toContain('overflow-y: auto')
  })

  it('全站无 >360px 的固定宽度/最小宽度（375px 视口可容纳）', () => {
    const declarations = cleanCss(CSS).matchAll(/(?:^|[;{\s])((?:min-)?width):\s*(\d{3,4})px/g)
    const oversized: string[] = []
    for (const match of declarations) {
      const value = Number(match[2])
      if (value > 360) {
        oversized.push(`${match[1]}:${match[2]}px`)
      }
    }
    expect(oversized).toEqual([])
    expect(cleanCss(CSS)).toContain('min-width: 320px')
  })
})

describe('窄屏对局页 DOM 钩子（流式面板/长文/取消按钮）', () => {
  const VIEW: GamePlayerViewResponse = {
    game_id: 'g1',
    turn: 3,
    version: 3,
    snapshot_version: 2,
    status: 'active',
    scenario: 'story',
    hp: 18,
    max_hp: 20,
    inventory: [],
    room: {
      room_id: 'room_entry',
      title: '哨站地窖',
      description: '',
      exits: {},
      items: [],
      npcs: [],
    },
    goal: null,
    ending_text: null,
    suggested_actions: [],
  }

  it('流式面板用 .btn 取消按钮、正文进 .turn-text-body（触控/换行样式按类生效）', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useGameStore()
    store.$patch({
      gameId: 'g1',
      turn: 3,
      version: 3,
      status: 'active',
      view: VIEW,
      streaming: true,
      streamText: '长流式文本'.repeat(40) + 'A'.repeat(120),
      history: [
        {
          id: 'turn:2',
          kind: 'narrative',
          text: '很长的叙事文本'.repeat(40) + 'B'.repeat(120),
          turnId: 2,
        },
      ],
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<div>home</div>' } },
        { path: '/game/:id', name: 'game', component: GameView },
        { path: '/game/:id/replay', name: 'replay', component: { template: '<div>replay</div>' } },
      ],
    })
    await router.push('/game/g1')
    await router.isReady()
    const wrapper = mount(GameView, { global: { plugins: [pinia, router] } })
    await flushPromises()

    const panel = wrapper.find('[data-testid="stream-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.find('.turn-text-body').text()).toContain('长流式文本')
    const cancel = wrapper.find('[data-testid="stream-cancel"]')
    expect(cancel.classes()).toContain('btn')
    expect(cancel.classes()).toContain('btn-small')
    // 长叙事在冒险记录中走 .history-text（窄屏 overflow-wrap:anywhere 生效）
    expect(wrapper.find('.history-text').exists()).toBe(true)
    expect(wrapper.find('.narrative-history').exists()).toBe(true)
    wrapper.unmount()
  })
})
