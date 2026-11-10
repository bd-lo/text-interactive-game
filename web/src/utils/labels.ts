/**
 * 展示层中文标签映射（M6 前端打磨）：回放元数据 event_type/method/actor_id → 中文；
 * 卡库 source/属性键与时间格式化也集中在此。
 *
 * 口径：只做**展示**转换，不改契约与数据；映射表覆盖当前领域枚举/固定场景 ID，
 * **未知值一律回退原值**（便于发现新增枚举，而不是静默吞掉）。
 */

import type { AttributeKey, CardSource } from '@/api/types'

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

/** 角色 ID 中文标签；未知回退原值。 */
export function labelActor(actorId: string): string {
  return ACTOR_LABELS[actorId] ?? actorId
}

/** 对局状态中文标签（服务端 `status`）。 */
export function labelGameStatus(status: 'active' | 'ended'): string {
  return status === 'active' ? '进行中' : '已结束'
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
