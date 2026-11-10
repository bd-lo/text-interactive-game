/**
 * 叙事历史纯函数（R2 包 A）：把 replay 事件还原成玩家可读的「冒险记录」。
 *
 * 口径（第 23 章 §23.3「不据前端推演权威状态」）：
 * - 数据源只用只读的 `GET /games/{id}/replay`（`result.narrative` 为叙事文本源）；
 * - **一个回合只取一条叙事**：服务端把同一回合玩家/NPC 事件都写入同一段 narrative
 *   （`turn_orchestrator` 多角色渲染实测 m3 同回合多事件文本相同），按 `seq` 升序取首个；
 * - 只收非空 `result.narrative`：`game_created`、失败回合不产生日志行；
 * - 失败回合用 `notice` 行追加（如「（该指令未生效）」），**绝不覆盖**已有叙事；
 * - 纯函数、不触网、不写状态，可被 vitest 直接单测。
 */

import type { ReplayEvent } from '@/api/types'

/** 日志行类型：`narrative` 回合叙事 / `notice` 系统提示。 */
export type HistoryEntryKind = 'narrative' | 'notice'

/** 一条冒险记录（渲染层只读展示）。 */
export interface HistoryEntry {
  /** 稳定 id：replay 重建行 `turn:<turnId>`，本会话行 `local:<n>`，系统行 `notice:<n>`。 */
  id: string
  kind: HistoryEntryKind
  text: string
  /** 回合号（系统行为 null）。 */
  turnId: number | null
}

/** 回合日志最多保留的会话内系统行数（防御 localStorage/长时间会话膨胀）。 */
export const MAX_HISTORY_ENTRIES = 500

/** 事件里的叙事文本；缺失/非字符串/空串一律视为无叙事。 */
export function narrativeOfEvent(event: ReplayEvent): string {
  const value = event.result.narrative
  return typeof value === 'string' ? value.trim() : ''
}

/** replay 事件 → 冒险记录（一回合一条，按全局 `seq` 升序）。 */
export function historyFromReplay(events: readonly ReplayEvent[]): HistoryEntry[] {
  const ordered = [...events].sort((left, right) => left.seq - right.seq)
  const entries: HistoryEntry[] = []
  const seenTurns = new Set<number>()
  for (const event of ordered) {
    if (seenTurns.has(event.turn_id)) {
      continue
    }
    const text = narrativeOfEvent(event)
    if (text === '') {
      continue
    }
    seenTurns.add(event.turn_id)
    entries.push({ id: `turn:${event.turn_id}`, kind: 'narrative', text, turnId: event.turn_id })
  }
  return entries
}

/** 追加一个成功回合的叙事（本会话即时可见；刷新后由 replay 重建）。 */
export function appendNarrative(
  history: readonly HistoryEntry[],
  input: { text: string; turnId: number | null },
): HistoryEntry[] {
  const text = input.text.trim()
  if (text === '') {
    return [...history]
  }
  const ordinal = history.filter((entry) => entry.kind === 'narrative').length + 1
  const next = [
    ...history,
    { id: `local:${ordinal}`, kind: 'narrative', text, turnId: input.turnId } as HistoryEntry,
  ]
  return next.length > MAX_HISTORY_ENTRIES ? next.slice(next.length - MAX_HISTORY_ENTRIES) : next
}

/** 追加一条「该指令未生效」系统行；不改写上一屏叙事。 */
export function appendNotice(
  history: readonly HistoryEntry[],
  text = '（该指令未生效）',
): HistoryEntry[] {
  const ordinal = history.filter((entry) => entry.kind === 'notice').length + 1
  return [
    ...history,
    { id: `notice:${ordinal}`, kind: 'notice', text, turnId: null } as HistoryEntry,
  ]
}

/** 用 replay 重建的叙事替换旧叙事，保留本会话系统行（按原相对顺序追加在末尾）。 */
export function mergeReplayHistory(
  rebuilt: readonly HistoryEntry[],
  current: readonly HistoryEntry[],
): HistoryEntry[] {
  const notices = current.filter((entry) => entry.kind === 'notice')
  return [...rebuilt, ...notices]
}
