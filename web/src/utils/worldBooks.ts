/**
 * 世界书前端工具（H4）：导入前客户端预检 + 错误人话 + 来源/触发语义展示文案。
 *
 * 口径：字段级校验一律以服务端 `POST /world-books/validate` / `import` 为准；
 * 这里只做「不必上传就能早点拦下」的基础检查（空/超 256KB/JSON 语法/根节点类型）。
 */

import { ApiClientError } from '@/api/client'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'

/** 上传契约上限：256KB（与后端 `MAX_UPLOAD_BYTES` 一致，H4-CONTRACT §2）。 */
export const WORLD_BOOK_MAX_BYTES = 256 * 1024

/** 文本预检结果。 */
export interface WorldBookTextCheck {
  ok: boolean
  error: string
  bytes: number
}

/** 文件大小预检（读文件前即可拒绝，避免无谓上传）。 */
export function checkWorldBookFileSize(size: number): string {
  if (size > WORLD_BOOK_MAX_BYTES) {
    return `文件过大：${size} 字节超过 256KB 上限（${WORLD_BOOK_MAX_BYTES} 字节）。`
  }
  return ''
}

/** 粘贴/文件 JSON 文本的基础预检（不替代服务端校验）。 */
export function checkWorldBookText(text: string): WorldBookTextCheck {
  const bytes = new TextEncoder().encode(text).length
  if (text.trim() === '') {
    return { ok: false, error: '内容为空：请选择世界书 JSON 文件或粘贴 JSON 内容。', bytes }
  }
  if (bytes > WORLD_BOOK_MAX_BYTES) {
    return { ok: false, error: `JSON 超过 256KB 上限（当前 ${bytes} 字节）。`, bytes }
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

/** 世界书来源 → 玩家可读标签（未知值回退原值，便于发现后端新增来源）。 */
export function labelWorldBookSource(source: string): string {
  if (source === 'tavern') {
    return '酒馆格式'
  }
  if (source === 'character_card') {
    return '角色卡内嵌'
  }
  if (source === 'manual') {
    return '简化格式'
  }
  return source
}

/** 校验预览里的「识别到的格式」→ 中文。 */
export function labelDetectedFormat(detected: string | undefined): string {
  if (detected === undefined || detected === '') {
    return '未知格式'
  }
  return labelWorldBookSource(detected)
}

/** `selectiveLogic` 数字 → 中文（0 AND_ANY / 1 NOT_ALL / 2 NOT_ANY / 3 AND_ALL）。 */
export function labelSelectiveLogic(value: number): string {
  if (value === 1) {
    return '非全部命中（NOT_ALL）'
  }
  if (value === 2) {
    return '全部未命中（NOT_ANY）'
  }
  if (value === 3) {
    return '全部命中（AND_ALL）'
  }
  return '任一命中（AND_ANY）'
}

/** `position` 数字 → 中文（SillyTavern 插入位置枚举；未知值回退「位置 N」）。 */
export function labelWorldBookPosition(value: number): string {
  if (value === 0) {
    return '角色设定前'
  }
  if (value === 1) {
    return '角色设定后'
  }
  if (value === 2) {
    return '作者注释前'
  }
  if (value === 3) {
    return '作者注释后'
  }
  if (value === 4) {
    return '按深度插入'
  }
  if (value === 5) {
    return '示例消息前'
  }
  if (value === 6) {
    return '示例消息后'
  }
  if (value === 7) {
    return '宏插入点'
  }
  return `位置 ${value}`
}

/** 定时效果（粘性/冷却/延迟）回合数 → 中文标签；0 表示关闭（返回空串）。 */
export function labelTimedEffect(turns: number, kind: 'sticky' | 'cooldown' | 'delay'): string {
  if (!Number.isFinite(turns) || turns <= 0) {
    return ''
  }
  const name = kind === 'sticky' ? '粘性' : kind === 'cooldown' ? '冷却' : '延迟'
  return `${name} ${Math.trunc(turns)} 回合`
}

/** 世界书相关错误的玩家可读文案（413 特例 + 其余复用统一映射）。 */
export function describeWorldBookError(error: unknown): PlayerErrorInfo {
  if (error instanceof ApiClientError && error.status === 413) {
    return {
      title: '文件太大了',
      message: '世界书 JSON 超过 256KB 上限，请精简后再导入。',
      detail: `${error.code}（HTTP 413）：${error.message}`,
    }
  }
  return describeError(error)
}
