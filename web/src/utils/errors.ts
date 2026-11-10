/**
 * 面向玩家的错误文案（R2 包 C）：把 6 章 §6.7 错误码 / 网络异常 / 422 校验项
 * 翻译成玩家看得懂的中文，技术细节只放「诊断」折叠区。
 *
 * 原则：
 * - 默认展示 `title + message`（人话），`detail` 仅在折叠区可见（含 error_code/HTTP 状态）；
 * - 未知错误回退通用文案，绝不把英文堆栈/裸 JSON 甩给玩家；
 * - 纯函数、可单测；不改错误契约（只读展示层）。
 */

import { ApiClientError } from '@/api/client'

/** 给玩家看的错误信息：标题 + 正文 + 技术细节（折叠展示）。 */
export interface PlayerErrorInfo {
  title: string
  message: string
  detail: string
}

/** 422 `violations[].loc` 末段 → 中文字段名（未知回退原值）。 */
const FIELD_LABELS: Record<string, string> = {
  seed: '随机种子',
  scenario: '场景',
  card_id: '人物卡',
  input: '指令',
  expected_version: '对局版本',
  file: '文件',
  body: '请求',
}

/** 422 `violations[].type` → 中文原因（未知回退原 msg）。 */
const VIOLATION_TYPE_LABELS: Record<string, string> = {
  int_parsing: '需要是整数',
  int_type: '需要是整数',
  string_type: '需要是文本',
  missing: '是必填项',
  greater_than_equal: '小于允许的最小值',
  less_than_equal: '大于允许的最大值',
  literal_error: '取值不在允许范围',
  too_long: '长度超出上限',
  too_short: '长度不足',
}

interface RawViolation {
  loc?: unknown
  type?: unknown
  msg?: unknown
}

/** `violations: [{loc,type,msg}]` → 可读行（如「随机种子：需要是整数」）。 */
export function describeViolations(violations: unknown): string[] {
  if (!Array.isArray(violations)) {
    return []
  }
  const lines: string[] = []
  for (const item of violations) {
    if (typeof item !== 'object' || item === null) {
      continue
    }
    const raw = item as RawViolation
    const loc = Array.isArray(raw.loc) ? raw.loc.map(String) : []
    const field = loc.length > 0 ? (FIELD_LABELS[loc[loc.length - 1] ?? ''] ?? loc[loc.length - 1] ?? '') : ''
    const typeKey = typeof raw.type === 'string' ? raw.type : ''
    const fallback = typeof raw.msg === 'string' ? raw.msg : ''
    const reason = VIOLATION_TYPE_LABELS[typeKey] ?? fallback
    if (reason === '') {
      continue
    }
    lines.push(field === '' || field === '请求' ? reason : `${field}：${reason}`)
  }
  return lines
}

/** 任意异常 → 玩家可读错误（标题/正文/技术细节）。 */
export function describeError(error: unknown): PlayerErrorInfo {
  if (error instanceof ApiClientError) {
    const detail = `${error.code}（HTTP ${error.status}）：${error.message}`
    if (error.code === 'TIMEOUT') {
      return { title: '网络有点慢', message: '请求超时了，请检查网络后重试。', detail }
    }
    if (error.status === 404 || error.code === 'NOT_FOUND') {
      return {
        title: '找不到这个对局',
        message: '这个对局可能不存在，或者对局 ID 输错了。',
        detail,
      }
    }
    if (error.status === 409 || error.code === 'CONFLICT') {
      return {
        title: '操作重复了',
        message: '这个请求之前已经处理过一次，请刷新页面后再试。',
        detail,
      }
    }
    const violations = describeViolations(error.violations)
    if (error.status === 422 || error.code === 'INVALID_INPUT' || error.code === 'SCHEMA_VIOLATION') {
      const reason = violations.length > 0 ? violations.join('；') : error.message
      return { title: '填写的内容不对', message: reason, detail }
    }
    if (error.code === 'NOT_ALLOWED' || error.code === 'OUT_OF_RANGE') {
      return { title: '这个操作现在不行', message: error.message, detail }
    }
    if (error.code === 'LLM_UNAVAILABLE') {
      return {
        title: '叙事服务暂时不可用',
        message: '已回退到离线叙事，可以继续游戏。',
        detail,
      }
    }
    return { title: '出了点问题', message: error.message, detail }
  }
  if (error instanceof TypeError) {
    return {
      title: '连接不上服务器',
      message: '请确认后端已在运行，然后重试。',
      detail: error.message,
    }
  }
  if (error instanceof Error) {
    return { title: '出了点问题', message: error.message, detail: error.message }
  }
  return { title: '出了点问题', message: String(error), detail: String(error) }
}

/** 回合业务失败（HTTP 200 + ok=false）→ 玩家可读信息。 */
export function describeTurnFailure(code: string | null, text: string): PlayerErrorInfo {
  const detail = `${code ?? 'INVALID_INPUT'}：${text}`
  const body = text === '' ? '这个动作没有生效。' : text
  if (code === 'INVALID_INPUT') {
    return { title: '这句指令没听懂', message: body, detail }
  }
  if (code === 'CONFLICT') {
    return {
      title: '对局在别处更新了',
      message: '这个对局刚刚在别处推进了，已同步到最新，请重新发送指令。',
      detail,
    }
  }
  if (code === 'NOT_ALLOWED' || code === 'OUT_OF_RANGE') {
    return { title: '这个动作现在做不了', message: body, detail }
  }
  if (code === 'NOT_FOUND') {
    return { title: '找不到这个对局', message: body, detail }
  }
  return { title: '这个动作没有生效', message: body, detail }
}

/** 诊断区展示用：错误码 + 技术细节（仅折叠区可见）。 */
export function diagnosticOf(info: PlayerErrorInfo): string {
  return info.detail
}
