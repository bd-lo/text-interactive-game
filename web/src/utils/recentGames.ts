/**
 * 最近对局（localStorage，R2 包 C）：服务端存档列表读不到时的**本机兜底** + 首页快速续玩。
 *
 * 口径：
 * - 只存最小展示字段（game_id/场景/回合/版本/最后叙事/打开时间），不存任何权威状态；
 * - `localStorage` 不可用（隐私模式/配额满）时静默降级为空列表，绝不抛错影响开局；
 * - 纯函数 + 容错解析，可直接单测（注入 `now` 保证确定性）。
 */

import type { Scenario } from '@/api/types'

/** localStorage 键名（升级存储结构时改版本后缀）。 */
export const RECENT_GAMES_KEY = 'textgame:recent-games'

/** 最多保留的最近对局数。 */
export const MAX_RECENT_GAMES = 8

/** 一条本机最近对局记录（展示用，非权威状态）。 */
export interface RecentGameRecord {
  gameId: string
  scenario: string
  turn: number
  version: number
  lastText: string
  /** ISO 时间（本机打开/推进该对局的时间）。 */
  savedAt: string
  seed: number | null
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function storage(): StorageLike | null {
  try {
    return globalThis.localStorage
  } catch {
    return null
  }
}

/** 记录类型校验（防手改 localStorage 造成首页崩溃）。 */
function isRecord(value: unknown): value is RecentGameRecord {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.gameId === 'string' &&
    record.gameId !== '' &&
    typeof record.scenario === 'string' &&
    typeof record.turn === 'number' &&
    typeof record.version === 'number' &&
    typeof record.lastText === 'string' &&
    typeof record.savedAt === 'string'
  )
}

/** 读取本机最近对局（按时间倒序、gameId 去重、上限 8 条；损坏数据静默丢弃）。 */
export function loadRecentGames(store: StorageLike | null = storage()): RecentGameRecord[] {
  if (store === null) {
    return []
  }
  try {
    const raw = store.getItem(RECENT_GAMES_KEY)
    if (raw === null || raw === '') {
      return []
    }
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }
    const byId = new Map<string, RecentGameRecord>()
    for (const item of parsed) {
      if (isRecord(item) && !byId.has(item.gameId)) {
        byId.set(item.gameId, item)
      }
    }
    return [...byId.values()]
      .sort((left, right) => right.savedAt.localeCompare(left.savedAt))
      .slice(0, MAX_RECENT_GAMES)
  } catch {
    return []
  }
}

/** 写入/更新一条最近对局（upsert、时间倒序、上限 8 条）。 */
export function rememberRecentGame(
  record: Omit<RecentGameRecord, 'savedAt'> & { savedAt?: string },
  options: { store?: StorageLike | null; now?: Date } = {},
): RecentGameRecord[] {
  const store = options.store === undefined ? storage() : options.store
  const now = options.now ?? new Date()
  const entry: RecentGameRecord = {
    gameId: record.gameId,
    scenario: record.scenario,
    turn: record.turn,
    version: record.version,
    lastText: record.lastText,
    seed: record.seed,
    savedAt: record.savedAt ?? now.toISOString(),
  }
  const next = [entry, ...loadRecentGames(store).filter((item) => item.gameId !== entry.gameId)].slice(
    0,
    MAX_RECENT_GAMES,
  )
  if (store !== null) {
    try {
      store.setItem(RECENT_GAMES_KEY, JSON.stringify(next))
    } catch {
      // 配额满/隐私模式：降级为仅内存列表，不阻塞开局
    }
  }
  return next
}

/** 从本机最近对局中移除一条（删除/无效对局时调用）。 */
export function forgetRecentGame(
  gameId: string,
  store: StorageLike | null = storage(),
): RecentGameRecord[] {
  const next = loadRecentGames(store).filter((item) => item.gameId !== gameId)
  if (store !== null) {
    try {
      store.setItem(RECENT_GAMES_KEY, JSON.stringify(next))
    } catch {
      // 同上，静默降级
    }
  }
  return next
}

/** ISO 时间 → 相对时间（刚刚/8 分钟前/昨天 23:10/11-08 04:12）。 */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) {
    return ''
  }
  const diffMs = now.getTime() - time.getTime()
  const minute = 60_000
  if (diffMs < minute) {
    return '刚刚'
  }
  if (diffMs < 60 * minute) {
    return `${Math.floor(diffMs / minute)} 分钟前`
  }
  const pad = (value: number): string => String(value).padStart(2, '0')
  const sameDay =
    time.getFullYear() === now.getFullYear() &&
    time.getMonth() === now.getMonth() &&
    time.getDate() === now.getDate()
  if (sameDay) {
    return `今天 ${pad(time.getHours())}:${pad(time.getMinutes())}`
  }
  const yesterday = new Date(now.getTime() - 24 * 60 * minute)
  const isYesterday =
    time.getFullYear() === yesterday.getFullYear() &&
    time.getMonth() === yesterday.getMonth() &&
    time.getDate() === yesterday.getDate()
  if (isYesterday) {
    return `昨天 ${pad(time.getHours())}:${pad(time.getMinutes())}`
  }
  return `${pad(time.getMonth() + 1)}-${pad(time.getDate())} ${pad(time.getHours())}:${pad(time.getMinutes())}`
}

/** 一行式摘要：`余烬哨站（剧情） · 第 3 回合 · 8 分钟前`。 */
export function describeRecentGame(
  record: Pick<RecentGameRecord, 'scenario' | 'turn' | 'savedAt'>,
  options: { scenarioLabel?: (scenario: string) => string; now?: Date } = {},
): string {
  const label = options.scenarioLabel?.(record.scenario) ?? record.scenario
  const time = formatRelativeTime(record.savedAt, options.now ?? new Date())
  return `${label} · 第 ${record.turn} 回合${time === '' ? '' : ` · ${time}`}`
}

/** 场景 ID → `Scenario` 联合类型收窄（用于把服务端字符串安全喂给建局请求）。 */
export function asScenario(value: string): Scenario | null {
  return value === 'm2' || value === 'm3' || value === 'm4' || value === 'story' ? value : null
}
