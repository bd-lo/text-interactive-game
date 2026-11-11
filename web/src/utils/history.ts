/**
 * 叙事历史纯函数（R2 包 A + R3 开场/结局派生行）：把 replay 事件还原成玩家可读的「冒险记录」。
 *
 * 口径（第 23 章 §23.3「不据前端推演权威状态」）：
 * - 数据源只用只读的 `GET /games/{id}/replay`（`result.narrative` 为叙事文本源）；
 * - **一个回合只取一条叙事**：服务端把同一回合玩家/NPC 事件都写入同一段 narrative
 *   （`turn_orchestrator` 多角色渲染实测 m3 同回合多事件文本相同），按 `seq` 升序取首个；
 * - 只收非空 `result.narrative`：`game_created`、失败回合不产生日志行；
 * - 失败回合用 `notice` 行追加（如「（该指令未生效）」），**绝不覆盖**已有叙事；
 * - R3 派生行只做展示：空局插「开场」（`withOpening`）、`ended` 追加「结局」（`withEnding`），
 *   固定 id 幂等（重复计算/刷新不重复渲染），不改 store；
 * - 纯函数、不触网、不写状态，可被 vitest 直接单测。
 */

import type { GamePlayerViewResponse, GameStatus, ReplayEvent } from '@/api/types'

/** 日志行类型：`narrative` 回合叙事 / `notice` 系统提示 / `opening` 开场 / `ending` 结局。 */
export type HistoryEntryKind = 'narrative' | 'notice' | 'opening' | 'ending'

/** 一条冒险记录（渲染层只读展示）。 */
export interface HistoryEntry {
  /** 稳定 id：replay 重建行 `turn:<turnId>`，本会话行 `local:<n>`，系统行 `notice:<n>`，
   *  派生行 `opening` / `ending`（幂等，重复派生不重复渲染）。 */
  id: string
  kind: HistoryEntryKind
  text: string
  /** 回合号（系统行/开场行/结局行为 null）。 */
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

// ---------------------------------------------------------------------------
// R3 派生日志行（开场 / 结局）：纯展示层派生，不写 store、幂等、刷新后自动恢复
// ---------------------------------------------------------------------------

/** 开场派生行 id（固定 → 重复派生不重复渲染）。 */
export const OPENING_ENTRY_ID = 'opening'
/** 结局派生行 id（固定 → 重复派生不重复渲染）。 */
export const ENDING_ENTRY_ID = 'ending'

/**
 * 新局开场文本（P2-2）：`/view.room.description` + 目标引导（有 goal 时附标题与进度）。
 * 无房间描述时返回空串（不伪造开场内容）。
 */
export function openingTextOf(view: GamePlayerViewResponse): string {
  const description = view.room.description.trim()
  if (description === '') {
    return ''
  }
  const goal = view.goal ?? null
  if (goal === null) {
    return description
  }
  const done = goal.objectives.filter((objective) => objective.done).length
  return `${description}\n\n目标：${goal.title}（进度 ${done}/${goal.objectives.length}）`
}

/**
 * 新局开场行（P2-2 + H4 修正）：满足以下任一条件时插入第 1 条「开场」：
 * - 冒险记录为空；或
 * - 记录里**只有新局首个回合（turnId ≤ 1）的只读回合正文**（看/背包等零事件回合不推进回合，
 *   `TurnResult.turn_id` 仍为 1）。此时若把开场行撤掉，玩家会看到「记录条数没有变化」，
 *   且丢失开局房间描述；保留开场直到出现真正推进回合的事件回合（turnId ≥ 2）。
 *
 * 旧行为（R3）是「有记录后立刻移除开场」；H4 起放宽到「首个事件回合后才移除」，
 * 旧行为测试（turnId=2 的记录不插开场）仍然成立。
 */
export function withOpening(
  history: readonly HistoryEntry[],
  view: GamePlayerViewResponse | null,
): HistoryEntry[] {
  if (view === null) {
    return [...history]
  }
  const onlyFreshReadonlyRows =
    history.length > 0 &&
    history.every(
      (entry) =>
        entry.kind === 'narrative' && entry.turnId !== null && entry.turnId <= 1,
    )
  if (history.length > 0 && !onlyFreshReadonlyRows) {
    return [...history]
  }
  const text = openingTextOf(view)
  if (text === '') {
    return [...history]
  }
  return [
    { id: OPENING_ENTRY_ID, kind: 'opening', text, turnId: null },
    ...history,
  ]
}

/**
 * 终局「结局」行（AC-10d）：`status=ended` 且有 `ending_text` 时追加到日志末尾，
 * 让玩家在冒险记录里也看到结局（不只底部横幅）。固定 id → 重复调用不重复渲染。
 */
export function withEnding(
  history: readonly HistoryEntry[],
  options: { status: GameStatus; endingText: string | null | undefined },
): HistoryEntry[] {
  const text = (options.endingText ?? '').trim()
  if (options.status !== 'ended' || text === '') {
    return [...history]
  }
  const withoutEnding = history.filter((entry) => entry.id !== ENDING_ENTRY_ID)
  return [
    ...withoutEnding,
    { id: ENDING_ENTRY_ID, kind: 'ending', text, turnId: null },
  ]
}

/**
 * 冒险记录展示序列：开场（空局才插）→ 回合叙事 → 结局（ended 才追加）。
 * 纯派生（不改 store），刷新/重载同样成立；重复计算幂等。
 */
export function displayHistory(
  history: readonly HistoryEntry[],
  options: { view: GamePlayerViewResponse | null; status: GameStatus; endingText: string | null | undefined },
): HistoryEntry[] {
  const withOpeningEntries = withOpening(history, options.view)
  return withEnding(withOpeningEntries, { status: options.status, endingText: options.endingText })
}

/**
 * 「最近回应」展示文本（R-23 配套：零事件只读回合的可见出口）。
 *
 * R-23 后冒险记录 = 事件记录：看/背包等零事件回合没有 `Event`，不写入 history，
 * 正文只存在于 `store.lastText`（刷新后可由 `/games/{id}` 快照的 `last_text` 恢复）。
 * 规则：`lastText` 去空白后为空 → 不展示；与记录里最近一条叙事（含「开场」「结局」
 * 派生行）正文相同 → 说明已写入冒险记录，不重复展示；否则返回该正文。
 */
export function lastResponseText(lastText: string, entries: readonly HistoryEntry[]): string {
  const text = lastText.trim()
  if (text === '') {
    return ''
  }
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index]
    if (entry === undefined) {
      continue
    }
    if (entry.kind === 'narrative' || entry.kind === 'opening' || entry.kind === 'ending') {
      return entry.text.trim() === text ? '' : text
    }
  }
  return text
}
