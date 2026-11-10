/**
 * 卡库导入前端的客户端预检与错误映射（M6-026，第 23 章 §23.5）。
 *
 * 不引 Ajv：这里只做**基础结构提示**（空/超 64KB/JSON 语法/根不是对象）；字段级校验
 * 一律以服务端 `POST /character-cards/validate` 为准（服务端 Pydantic 权威）。
 */

import { ApiClientError } from '@/api/client'

/** 卡库上传契约上限：64KB（与 `CardService.MAX_UPLOAD_BYTES` 一致）。 */
export const CARD_MAX_BYTES = 64 * 1024

export interface CardTextCheck {
  ok: boolean
  error: string
  bytes: number
}

/** 文件大小预检（读文件之前即可拒绝，避免无谓上传）。 */
export function checkCardFileSize(size: number): string {
  if (size > CARD_MAX_BYTES) {
    return `文件过大：${size} 字节超过 64KB 上限（${CARD_MAX_BYTES} 字节）。`
  }
  return ''
}

/** 粘贴/文件 JSON 文本的基础预检（不替代服务端 validate）。 */
export function checkCardText(text: string): CardTextCheck {
  const bytes = new TextEncoder().encode(text).length
  if (text.trim() === '') {
    return { ok: false, error: 'JSON 内容为空：请粘贴人物卡 JSON 或选择 .json 文件。', bytes }
  }
  if (bytes > CARD_MAX_BYTES) {
    return { ok: false, error: `JSON 超过 64KB 上限（当前 ${bytes} 字节）。`, bytes }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: 'JSON 语法错误：无法解析，请检查括号/引号/逗号。', bytes }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: 'JSON 根节点必须是对象（{...}）。', bytes }
  }
  return { ok: true, error: '', bytes }
}

export interface CardErrorInfo {
  message: string
  code: string
  status: number
}

/** 统一失败信封 → 面向玩家的中文错误提示（413/422/403/409/404 特例）。 */
export function describeCardError(error: unknown): CardErrorInfo {
  if (error instanceof ApiClientError) {
    if (error.status === 413) {
      return { message: '文件过大（413）：人物卡 JSON 超过 64KB 上限。', code: error.code, status: 413 }
    }
    if (error.status === 403) {
      return { message: `内容安全校验未通过（403）：${error.message}`, code: error.code, status: 403 }
    }
    if (error.status === 409) {
      return {
        message: '幂等冲突（409）：同一 Idempotency-Key 已用于不同内容，请修改内容后重试（下次自动换新键）。',
        code: error.code,
        status: 409,
      }
    }
    if (error.status === 422) {
      // 世界书引导（H4 WB-03b）：卡库只收 9 字段纯人物卡；角色卡 V2 包装（含
      // data.character_book / spec_version）被服务端拒绝时，要明确告诉玩家去世界书导入。
      const violationText = formatViolations(error.violations).join('；')
      const combined = `${error.message} ${violationText}`
      if (/character_book|spec_version|character card v2/i.test(combined)) {
        return {
          message:
            '这看起来是世界书文件（422）：卡库只收 9 字段纯人物卡；含 data.character_book 的角色卡 V2 请到「世界书 → 导入世界书」。',
          code: error.code,
          status: 422,
        }
      }
      return { message: `人物卡不合法（422）：${error.message}`, code: error.code, status: 422 }
    }
    if (error.status === 404) {
      // 玩家可见文案不露内部 card_id（R2 AC-23：服务端 message 可能带 ID，这里只给可读原因）
      return { message: '人物卡不存在（404）：可能已被删除，或者链接有误。', code: error.code, status: 404 }
    }
    return { message: `${error.code}（${error.status}）：${error.message}`, code: error.code, status: error.status }
  }
  return {
    message: error instanceof Error ? error.message : String(error),
    code: 'INTERNAL',
    status: 0,
  }
}

/** 统一信封 `violations`（`[{loc, type, msg}]`）→ 可展示的行文本。 */
export function formatViolations(violations: unknown): string[] {
  if (!Array.isArray(violations)) {
    return []
  }
  const lines: string[] = []
  for (const item of violations) {
    if (typeof item !== 'object' || item === null) {
      continue
    }
    const record = item as Record<string, unknown>
    const loc = Array.isArray(record.loc) ? record.loc.map(String).join('.') : ''
    const msg = typeof record.msg === 'string' ? record.msg : ''
    if (msg === '') {
      continue
    }
    lines.push(loc === '' ? msg : `${loc}：${msg}`)
  }
  return lines
}
