/**
 * 玩家建议指令（R2 包 B/C）：从只读 `/view` 派生**确定性的**「可以试试」。
 *
 * 背景：后端成功回合的 `suggested_actions` 恒为空（失败回合才有），而失败回合的建议里
 * 还混着「刷新对局视图」这类**应用动作**（不是游戏指令）。前端不猜剧情，只按当前可见
 * 事实派生玩家一定做得到的动作：出口 → 移动、地上物品 → 拿起、房里 NPC → 问好、
 * 最后兜底「环顾四周 / 查看背包」。
 *
 * 纯函数、不触网、可单测；输出顺序稳定（出口 → 物品 → NPC → 兜底）。
 */

import type { GamePlayerViewResponse } from '@/api/types'
import { directionLabel } from '@/utils/labels'

/** 单条建议：玩家点一下就会提交的指令文本。 */
export interface CommandSuggestion {
  /** 展示文本（通常就是指令本身）。 */
  label: string
  /** 提交给 `/turns` 的玩家指令。 */
  command: string
  /** 来源（仅测试/调试用，不渲染）。 */
  kind: 'exit' | 'item' | 'npc' | 'basic'
}

/** 兜底建议：任何房间都成立的基础动作。 */
const FALLBACK_COMMANDS: readonly CommandSuggestion[] = [
  { label: '环顾四周', command: '环顾四周', kind: 'basic' },
  { label: '查看背包', command: '查看背包', kind: 'basic' },
]

/** 出口 → 「向北走」（未知方位回退「去<原始方向>」，不吞未知值）。 */
export function exitSuggestions(view: GamePlayerViewResponse | null): CommandSuggestion[] {
  if (view === null) {
    return []
  }
  return Object.keys(view.room.exits).map((direction) => {
    const known = directionLabel(direction) !== direction
    return {
      label: known ? `向${directionLabel(direction)}走` : `去${direction}`,
      command: known ? `向${directionLabel(direction)}走` : `去${direction}`,
      kind: 'exit',
    }
  })
}

/** 出口指令 → 目标房间展示名（`room.exit_names` D9 加法字段；没有则不显示提示）。 */
export function exitTitles(view: GamePlayerViewResponse | null): Record<string, string> {
  if (view === null) {
    return {}
  }
  const names = view.room.exit_names ?? {}
  const titles: Record<string, string> = {}
  for (const suggestion of exitSuggestions(view)) {
    const direction = Object.keys(view.room.exits).find((key) => {
      const known = directionLabel(key) !== key
      return (known ? `向${directionLabel(key)}走` : `去${key}`) === suggestion.command
    })
    const target = direction === undefined ? undefined : names[direction]
    if (target !== undefined && target !== '') {
      titles[suggestion.command] = `通往${target}`
    }
  }
  return titles
}

/** 房间可见物品 → 「拿起火把」。 */
export function itemSuggestions(view: GamePlayerViewResponse | null): CommandSuggestion[] {
  if (view === null) {
    return []
  }
  return view.room.items.map((item) => ({
    label: `拿起${item.name}`,
    command: `拿起${item.name}`,
    kind: 'item',
  }))
}

/** 房间可见 NPC → 「对老哨兵问好」（离线解析器关键词：说/问/聊/喊/交谈）。 */
export function npcSuggestions(view: GamePlayerViewResponse | null): CommandSuggestion[] {
  if (view === null) {
    return []
  }
  return view.room.npcs.map((npc) => ({
    label: `对${npc.name}问好`,
    command: `对${npc.name}问好`,
    kind: 'npc',
  }))
}

/** 去重（按 command）并截断到上限。 */
function dedupe(suggestions: readonly CommandSuggestion[], limit: number): CommandSuggestion[] {
  const seen = new Set<string>()
  const result: CommandSuggestion[] = []
  for (const suggestion of suggestions) {
    if (suggestion.command === '' || seen.has(suggestion.command)) {
      continue
    }
    seen.add(suggestion.command)
    result.push(suggestion)
    if (result.length >= limit) {
      break
    }
  }
  return result
}

/** 完整建议列表（出口 → 物品 → NPC → 兜底；去重、上限 5 条）。 */
export function suggestCommands(
  view: GamePlayerViewResponse | null,
  limit = 5,
): CommandSuggestion[] {
  const contextual = [
    ...exitSuggestions(view),
    ...itemSuggestions(view),
    ...npcSuggestions(view),
  ]
  return dedupe([...contextual, ...FALLBACK_COMMANDS], limit)
}

/** 完整建议的指令文本（供「可以试试」chips 直接渲染/提交）。 */
export function suggestActions(view: GamePlayerViewResponse | null, limit = 5): string[] {
  return suggestCommands(view, limit).map((suggestion) => suggestion.command)
}

/** 失败回合建议里混入的应用动作（不是游戏指令，UI 需单独处理）。 */
export const APP_ACTION_REFRESH = '刷新对局视图'

/** 是否为应用动作而非游戏指令（避免把「刷新对局视图」当指令提交）。 */
export function isAppAction(command: string): boolean {
  return command === APP_ACTION_REFRESH
}
