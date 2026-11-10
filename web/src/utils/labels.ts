/**
 * 展示层中文标签映射（M6 前端打磨）：回放元数据 event_type/method/actor_id → 中文；
 * 卡库 source/属性键与时间格式化也集中在此。
 *
 * 口径：只做**展示**转换，不改契约与数据；映射表覆盖当前领域枚举/固定场景 ID，
 * **未知值一律回退原值**（便于发现新增枚举，而不是静默吞掉）。
 */

import type { AttributeKey, CardSource, Goal } from '@/api/types'

/** 人物卡属性键 → 中文（第 5 章 §5.7，恰好 7 键）。 */
export const ATTRIBUTE_LABELS: Record<AttributeKey, string> = {
  str: '力量',
  dex: '敏捷',
  con: '体质',
  int: '智力',
  wis: '感知',
  cha: '魅力',
  max_hp: '生命上限',
}

/** 人物卡来源 → 中文。 */
export function labelCardSource(source: CardSource): string {
  if (source === 'upload') {
    return '上传'
  }
  if (source === 'llm') {
    return 'LLM 生成'
  }
  return '手动'
}

/**
 * 内置物品模板键 → 中文名（与后端 `domain/character_card.py` 的 `ITEM_TEMPLATES` 同步）。
 *
 * 人物卡 `starting_items` 契约存的是**模板键**（如 `item.torch`）；直接展示会向玩家暴露内部键，
 * 因此卡库详情/生成草稿预览统一走本映射（未知模板键回退原值，兼容自定义上传卡）。
 */
const ITEM_TEMPLATE_LABELS: Record<string, string> = {
  'item.torch': '火把',
  'item.potion': '治疗药水',
  'item.sword': '锈剑',
  'item.relic': '古物',
  'item.rusty_sword': '锈剑',
  'item.rusty_knife': '锈刀',
  'item.chalk': '粉笔',
  'item.oak_cane': '橡木手杖',
  'item.iron_ingot': '铁锭',
  'item.oak_handle': '橡木柄',
  'item.forge_hammer': '锻锤',
  'item.copper_wire': '铜线',
}

/** 物品模板键 → 中文名；`item_<name>` 写法与 `item.<name>` 等价，未知回退原键。 */
export function labelItemTemplate(templateId: string): string {
  const direct = ITEM_TEMPLATE_LABELS[templateId]
  if (direct !== undefined) {
    return direct
  }
  if (templateId.startsWith('item_')) {
    return ITEM_TEMPLATE_LABELS[`item.${templateId.slice('item_'.length)}`] ?? templateId
  }
  return templateId
}

/** 初始物品列表 → 中文名列表（不改变数量/顺序）。 */
export function labelItemTemplates(templateIds: readonly string[]): string[] {
  return templateIds.map((templateId) => labelItemTemplate(templateId))
}

/** 结局屏通关总结的数据源（全部来自 `GET /view`，不新增后端字段）。 */
export interface EndingSummaryInput {
  turn: number
  hp: number
  maxHp: number
  goal: Goal | null
}

/**
 * 结局屏通关总结：「本局 N 回合 · 目标 X/Y · 生命值 HP/MAX」（AC-34）。
 * 非剧情场景无 goal 时省略目标段（不伪造 0/0）。
 */
export function formatEndingSummary(input: EndingSummaryInput): string {
  const parts = [`本局 ${input.turn} 回合`]
  const goal = input.goal
  if (goal !== null) {
    const done = goal.objectives.filter((objective) => objective.done).length
    parts.push(`目标 ${done}/${goal.objectives.length}`)
  }
  parts.push(`生命值 ${input.hp}/${input.maxHp}`)
  return parts.join(' · ')
}

/** ISO 时间串 → 可读展示（`2026-11-10 13:00:00 UTC`）。 */
export function formatDateTime(value: string): string {
  return value.replace('T', ' ').replace('Z', ' UTC').replace(/\.\d+/, '')
}

/** `EventType`（第 14 章 §14.7；`game_created` 为小写历史值）。 */
const EVENT_TYPE_LABELS: Record<string, string> = {
  game_created: '游戏创建',
  CHARACTER_MOVED: '角色移动',
  ITEM_TAKEN: '拾取物品',
  ITEM_CONSUMED: '使用物品',
  DIALOGUE_EXCHANGED: '对话',
  CRAFT_DETERMINISTIC: '制造（确定性）',
  CRAFT_GENERATED: '自由制造',
  CRAFT_REJECTED: '制造被拒绝',
  ADMIN_CORRECTION: '管理员修正',
}

/** 事件写入方 method（工具/服务层契约值）。 */
const METHOD_LABELS: Record<string, string> = {
  game_created: '创建存档',
  move_to: '移动',
  take_item: '拾取',
  use_item: '使用物品',
  talk_to: '交谈',
  craft_item: '制造',
  look: '观察',
  get_inventory: '查看背包',
}

/** 固定 actor ID → 中文（第 5 章/场景常量，golden 固化；仅展示用）。 */
const ACTOR_LABELS: Record<string, string> = {
  player: '玩家',
  char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN: '玩家',
  npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP: '老哨兵',
  char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV: '拾荒者甲',
  char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJW: '拾荒者乙',
  system: '系统',
}

/** 事件类型中文标签；`null`/空串显示占位，未知回退原值。 */
export function labelEventType(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') {
    return '（无）'
  }
  return EVENT_TYPE_LABELS[value] ?? value
}

/** 事件方法中文标签；未知回退原值。 */
export function labelMethod(value: string): string {
  return METHOD_LABELS[value] ?? value
}

/** 内部实体 ID 形式（`char_/npc_/item_/room_/game_` + Crockford Base32 ULID）；绝不直接展示给玩家。 */
const INTERNAL_ENTITY_ID = /^(?:char|npc|item|room|game)_[0-9A-HJKMNP-TV-Z]{6,}$/

/** 角色 ID 中文标签；未知**内部 ULID** 统一显示「其他角色」（不把内部 ID 甩给玩家），其余未知回退原值。 */
export function labelActor(actorId: string): string {
  const known = ACTOR_LABELS[actorId]
  if (known !== undefined) {
    return known
  }
  return INTERNAL_ENTITY_ID.test(actorId) ? '其他角色' : actorId
}

/** 对局状态中文标签（服务端 `status`）。 */
export function labelGameStatus(status: 'active' | 'ended'): string {
  return status === 'active' ? '进行中' : '已结束'
}

/** 场景 ID → 玩家可读名称（`POST /games` 的 `scenario`）。 */
const SCENARIO_LABELS: Record<string, string> = {
  story: '余烬哨站（剧情）',
  m2: '废弃哨站（入门）',
  m3: '拾荒者营地（多角色）',
  m4: '工坊（制造）',
}

/** 场景中文标签；未知值回退原值。 */
export function labelScenario(scenario: string): string {
  return SCENARIO_LABELS[scenario] ?? scenario
}

/** 出口方向键（服务端 `room.exits` 的英文方位）→ 中文。 */
const DIRECTION_LABELS: Record<string, string> = {
  north: '北',
  south: '南',
  east: '东',
  west: '西',
  northeast: '东北',
  northwest: '西北',
  southeast: '东南',
  southwest: '西南',
  up: '上',
  down: '下',
  in: '里',
  out: '外',
}

/** 方位中文标签；未知值回退原值（便于发现新增方向而不是静默吞掉）。 */
export function directionLabel(direction: string): string {
  return DIRECTION_LABELS[direction] ?? direction
}

/** 事件序号可读展示：`事件 #12`。 */
export function formatEventSeq(seq: number): string {
  return `事件 #${seq}`
}

/** 回合号可读展示：`第 2 回合`。 */
export function formatTurn(turn: number): string {
  return `第 ${turn} 回合`
}

/** 回放定位一行式：`第 2 回合 · 事件 #3`。 */
export function formatEventRef(seq: number, turn: number): string {
  return `${formatTurn(turn)} · ${formatEventSeq(seq)}`
}
