/**
 * 用户自上传场景前端工具（scenario modules v1）：导入前客户端预检 + 错误人话 +
 * 统计摘要/原始定义的安全只读展示辅助。
 *
 * 口径（`docs/design/user-scenario-modules.md` §2.3/§3.4）：
 * - 字段级校验一律以服务端 `POST /api/v1/scenarios/validate` 与导入端点为准；
 *   这里只做「不必上传就能早点拦下」的基础检查（空/超 256KB/非 UTF-8/JSON 语法/根节点类型）；
 * - 文本按纯文本处理（页面只用插值，禁富文本注入）；场景定义是玩家上传的任意 JSON，
 *   展示辅助必须容错（缺字段不炸，未知值回退原值）。
 */

import { ApiClientError } from '@/api/client'
import type {
  ScenarioDefinition,
  ScenarioStats,
  ScenarioWarning,
} from '@/api/types'
import { describeError, describeViolations, type PlayerErrorInfo } from '@/utils/errors'

/** 上传契约上限：256KB（与后端 `MAX_UPLOAD_BYTES` 一致，场景设计 §2.3）。 */
export const SCENARIO_MAX_BYTES = 256 * 1024

/** 文本预检结果（客户端基础检查，不代表服务端校验通过）。 */
export interface ScenarioTextCheck {
  ok: boolean
  error: string
  bytes: number
}

/** 文件大小预检（读文件前即可拒绝，避免无谓上传）。 */
export function checkScenarioFileSize(size: number): string {
  if (size > SCENARIO_MAX_BYTES) {
    return `文件过大：${size} 字节超过 256KB 上限（${SCENARIO_MAX_BYTES} 字节）。`
  }
  return ''
}

/** 文件名预检：仅接受 `.json`（后端 multipart 契约要求 `*.json`）。 */
export function checkScenarioFileName(name: string): string {
  const trimmed = name.trim()
  if (trimmed === '') {
    return '文件没有名字：请重新选择一份 .json 场景文件。'
  }
  if (!/\.json$/i.test(trimmed)) {
    return `文件类型不对：只接受 .json 场景文件（当前：${trimmed}）。`
  }
  return ''
}

/**
 * 粘贴/读取文件后的 JSON 文本基础预检：
 * 空内容、256KB 上限、UTF-8 解码失败（`\uFFFD` 替换符）、JSON 语法、根节点必须是对象。
 */
export function checkScenarioText(text: string): ScenarioTextCheck {
  const bytes = new TextEncoder().encode(text).length
  if (text.trim() === '') {
    return { ok: false, error: '内容为空：请选择场景 JSON 文件或粘贴 JSON 内容。', bytes }
  }
  if (bytes > SCENARIO_MAX_BYTES) {
    return { ok: false, error: `JSON 超过 256KB 上限（当前 ${bytes} 字节）。`, bytes }
  }
  if (text.includes('\uFFFD')) {
    return {
      ok: false,
      error: '文件不是 UTF-8 编码：请用 UTF-8 保存后再导入。',
      bytes,
    }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: 'JSON 语法错误：无法解析，请检查括号/引号/逗号。', bytes }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: 'JSON 根节点必须是对象（{...}），不能是数组或基础类型。', bytes }
  }
  return { ok: true, error: '', bytes }
}

/**
 * 场景 422 `violations` 的可读行：场景设计契约里是 `[{path,message}]`（与 validate 的 errors 同构），
 * 兼容既有 `[{loc,type,msg}]` 形状；两种都没有时返回空数组（由调用方回退统一文案）。
 */
function scenarioViolationLines(violations: unknown): string[] {
  if (!Array.isArray(violations)) {
    return []
  }
  const lines: string[] = []
  for (const item of violations) {
    if (typeof item !== 'object' || item === null) {
      continue
    }
    const record = item as Record<string, unknown>
    if (typeof record.path === 'string' && typeof record.message === 'string') {
      lines.push(record.path === '' ? record.message : `${record.path}：${record.message}`)
      continue
    }
    const line = describeViolations([item])[0]
    if (line !== undefined) {
      lines.push(line)
    }
  }
  return lines
}

/** 场景相关错误的玩家可读文案（413/404/422 特例 + 其余复用统一映射）。 */
export function describeScenarioError(error: unknown): PlayerErrorInfo {
  if (error instanceof ApiClientError && error.status === 413) {
    return {
      title: '文件太大了',
      message: '场景 JSON 超过 256KB 上限，请精简后再导入。',
      detail: `${error.code}（HTTP 413）：${error.message}`,
    }
  }
  if (error instanceof ApiClientError && (error.status === 404 || error.code === 'NOT_FOUND')) {
    return {
      title: '找不到这个场景',
      message: '这个场景可能不存在，或者已经被删除了。',
      detail: `${error.code}（HTTP 404）：${error.message}`,
    }
  }
  if (
    error instanceof ApiClientError &&
    (error.status === 422 || error.code === 'INVALID_INPUT' || error.code === 'SCHEMA_VIOLATION')
  ) {
    const lines = scenarioViolationLines(error.violations)
    if (lines.length > 0) {
      return {
        title: '场景内容不符合格式',
        message: lines.join('；'),
        detail: `${error.code}（HTTP ${error.status}）：${error.message}`,
      }
    }
  }
  return describeError(error)
}

/** 统计摘要 → 一行中文（列表项/校验通过 panel 共用）。 */
export function formatScenarioStats(summary: ScenarioStats): string {
  return [
    `${summary.rooms} 个房间`,
    `${summary.items} 件物品`,
    `${summary.npcs} 个角色`,
    `${summary.objectives} 个目标`,
    `${summary.endings} 个结局`,
    `${summary.suggestions} 条建议`,
  ].join(' · ')
}

/** 告警/错误项 → 展示行：「位置 path：message」（根级提示只显示 message）。 */
export function formatScenarioIssue(item: { path: string; message: string }): string {
  const path = item.path.trim()
  return path === '' ? item.message : `${path}：${item.message}`
}

/** 结局 `kind` → 中文标签（只影响展示；未知回退原值）。 */
export function labelEndingKind(kind: string): string {
  if (kind === 'win') {
    return '胜利'
  }
  if (kind === 'lose') {
    return '失败'
  }
  if (kind === 'neutral') {
    return '中性'
  }
  return kind
}

/** 场景定义里的房间摘要（只读展示用；字段缺失时回退 key）。 */
export interface ScenarioRoomView {
  key: string
  name: string
  description: string
}

/** 场景定义里的物品摘要。 */
export interface ScenarioItemView {
  key: string
  name: string
  description: string
}

/** 场景定义里的 NPC 摘要（带所在房间展示名）。 */
export interface ScenarioNpcView {
  key: string
  name: string
  roomKey: string
  roomName: string
}

/** 目标的一条子目标摘要。 */
export interface ScenarioObjectiveView {
  key: string
  text: string
}

/** 场景目标摘要。 */
export interface ScenarioGoalView {
  title: string
  description: string
  objectives: ScenarioObjectiveView[]
}

/** 场景结局摘要。 */
export interface ScenarioEndingView {
  key: string
  kind: string
  kindLabel: string
  title: string
  text: string
}

/** 详情页「房间/物品/NPC/目标/结局」只读摘要（由 `definition` 容错派生）。 */
export interface ScenarioDefinitionSections {
  rooms: ScenarioRoomView[]
  items: ScenarioItemView[]
  npcs: ScenarioNpcView[]
  goal: ScenarioGoalView | null
  endings: ScenarioEndingView[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function pickString(record: Record<string, unknown>, key: string, fallback = ''): string {
  const value = record[key]
  return typeof value === 'string' ? value : fallback
}

/**
 * 从场景原始定义派生展示摘要（纯函数、容错）。
 *
 * 说明：字段级合法性由服务端校验保证；这里只负责把玩家可读 JSON 映射成列表，
 * 任何缺字段/类型不符都回退为空值，不让详情页因为一个怪字段整页崩。
 */
export function scenarioDefinitionSections(
  definition: ScenarioDefinition,
): ScenarioDefinitionSections {
  const root = asRecord(definition) ?? {}

  const rooms: ScenarioRoomView[] = asArray(root.rooms).flatMap((item) => {
    const room = asRecord(item)
    if (room === null) {
      return []
    }
    const key = pickString(room, 'key')
    return [
      {
        key,
        name: pickString(room, 'name', key === '' ? '（未命名房间）' : key),
        description: pickString(room, 'description'),
      },
    ]
  })
  const roomNameByKey = new Map<string, string>()
  for (const room of rooms) {
    if (room.key !== '') {
      roomNameByKey.set(room.key, room.name)
    }
  }

  const items: ScenarioItemView[] = asArray(root.items).flatMap((item) => {
    const entry = asRecord(item)
    if (entry === null) {
      return []
    }
    const key = pickString(entry, 'key')
    return [
      {
        key,
        name: pickString(entry, 'name', key === '' ? '（未命名物品）' : key),
        description: pickString(entry, 'description'),
      },
    ]
  })

  const npcs: ScenarioNpcView[] = asArray(root.npcs).flatMap((item) => {
    const npc = asRecord(item)
    if (npc === null) {
      return []
    }
    const key = pickString(npc, 'key')
    const roomKey = pickString(npc, 'room')
    return [
      {
        key,
        name: pickString(npc, 'name', key === '' ? '（未命名角色）' : key),
        roomKey,
        roomName: roomNameByKey.get(roomKey) ?? roomKey,
      },
    ]
  })

  const goalRecord = asRecord(root.goal)
  const goal: ScenarioGoalView | null =
    goalRecord === null
      ? null
      : {
          title: pickString(goalRecord, 'title'),
          description: pickString(goalRecord, 'description'),
          objectives: asArray(goalRecord.objectives).flatMap((item) => {
            const objective = asRecord(item)
            if (objective === null) {
              return []
            }
            return [
              {
                key: pickString(objective, 'key'),
                text: pickString(objective, 'text'),
              },
            ]
          }),
        }

  const endings: ScenarioEndingView[] = asArray(root.endings).flatMap((item) => {
    const ending = asRecord(item)
    if (ending === null) {
      return []
    }
    const key = pickString(ending, 'key')
    const kind = pickString(ending, 'kind')
    return [
      {
        key,
        kind,
        kindLabel: labelEndingKind(kind),
        title: pickString(ending, 'title', key === '' ? '（未命名结局）' : key),
        text: pickString(ending, 'text'),
      },
    ]
  })

  return { rooms, items, npcs, goal, endings }
}

/** 列表/详情 warnings 供模板直接使用的展示行（保留 path+message 结构）。 */
export function formatScenarioWarnings(warnings: readonly ScenarioWarning[]): string[] {
  return warnings.map((warning) => formatScenarioIssue(warning))
}
