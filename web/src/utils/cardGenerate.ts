/**
 * 生成人物卡展示层纯函数（M6-028，第 23 章 §23.6/§23.7）：
 * brief 码点计数、模板兜底判定、告警/校验错误中文化、生成/保存失败文案映射。
 *
 * 口径：
 * - brief 长度按**码点** `[...str].length` 计数（与第 5/23 章「禁止用 UTF-16 length 等价」一致）；
 * - 模板兜底判定 = `generated_by === null` **或** `warnings[].code === "TEMPLATE_FALLBACK"`（容错）；
 * - 面向玩家的文案全部中文、不出现内部 ID/裸英文错误码；技术细节只作为 `detail` 折叠展示；
 * - 纯函数、无副作用、可单测；不做任何自动重试/定时逻辑。
 */

import { ApiClientError } from '@/api/client'
import type {
  CharacterCard,
  CharacterCardValidationError,
  CharacterCardWarning,
} from '@/api/types'

/** brief 码点上限（与后端 `MAX_BRIEF_CHARS` 一致）。 */
export const GENERATE_BRIEF_MAX_CHARS = 1000

/** 模板兜底 warning 码（与后端 `TEMPLATE_FALLBACK_WARNING` 一致）。 */
export const TEMPLATE_FALLBACK_WARNING_CODE = 'TEMPLATE_FALLBACK'

/** `options.tone` 可选值（仅参与生成/幂等哈希，不执行其中的指令）。 */
export const GENERATE_TONE_OPTIONS = [
  { value: '', label: '不限（默认）' },
  { value: 'grim', label: '阴郁' },
  { value: 'warm', label: '温暖' },
  { value: 'mysterious', label: '神秘' },
] as const

/** 按**码点**计数（emoji 等代理对算 1 个字，与后端 Python len 口径一致）。 */
export function countBriefChars(text: string): number {
  return [...text].length
}

/** brief 提交前预检：trim 后非空且码点 ≤1000（与服务端「trim 后校验」口径一致）。 */
export function isBriefValid(text: string): boolean {
  const trimmed = text.trim()
  return trimmed !== '' && countBriefChars(trimmed) <= GENERATE_BRIEF_MAX_CHARS
}

/** warnings 是否包含模板兜底标记。 */
export function hasTemplateFallbackWarning(
  warnings: readonly CharacterCardWarning[] | undefined,
): boolean {
  if (warnings === undefined) {
    return false
  }
  return warnings.some((warning) => warning.code === TEMPLATE_FALLBACK_WARNING_CODE)
}

/**
 * 草稿是否为模板兜底（M6B-4 已知限制）：`generated_by === null` 或 warnings 含
 * `TEMPLATE_FALLBACK`；两者正常应同时成立，这里做容错（任一成立即判定兜底）。
 */
export function isTemplateFallback(
  generatedBy: string | null | undefined,
  warnings: readonly CharacterCardWarning[] | undefined,
): boolean {
  if (generatedBy === null || generatedBy === undefined) {
    return true
  }
  return hasTemplateFallbackWarning(warnings)
}

/** 草稿徽章文案：「模板草稿」/「LLM 草稿」。 */
export function draftBadgeLabel(templateFallback: boolean): string {
  return templateFallback ? '模板草稿' : 'LLM 草稿'
}

/** 生成告警 → 玩家可读中文（`TEMPLATE_FALLBACK` 用固定文案，不暴露英文错误码）。 */
export function describeGenerateWarning(warning: CharacterCardWarning): string {
  if (warning.code === TEMPLATE_FALLBACK_WARNING_CODE) {
    return '未调用 LLM，已按 brief 生成确定性模板草稿（来源=手动、生成者=无）；可以编辑后保存。'
  }
  if (warning.code === 'DUPLICATE_ITEM_MERGED') {
    return '初始物品有重复项，已自动合并为一件。'
  }
  return warning.message !== '' ? warning.message : '生成时有一条提示，但不影响保存。'
}

/** 校验错误项 → 「字段路径：说明」行（字段路径为空显示根）。 */
export function formatCardValidationError(error: CharacterCardValidationError): string {
  const loc = error.loc.length > 0 ? error.loc.join('.') : '（根）'
  return `${loc}：${error.msg}`
}

/** 草稿 → 编辑框预填 JSON（只读展示/可编辑均用格式化文本）。 */
export function prettyCardJson(card: CharacterCard): string {
  return JSON.stringify(card, null, 2)
}

/** 生成/保存失败 → 玩家可读文案 + 是否清幂等键（409 才清）。 */
export interface GenerateErrorInfo {
  title: string
  message: string
  detail: string
  code: string
  status: number
  retryAfterSeconds: number
  /** `true` 表示同键异参（409）：清键，下次手动提交自动换新键。 */
  clearKey: boolean
}

function technicalDetail(error: ApiClientError): string {
  return `${error.code}（HTTP ${error.status}）：${error.message}`
}

/**
 * 生成失败映射（第 23 章 §23.6-7）：429 按 `Retry-After` 提示等待秒数；
 * `SCHEMA_VIOLATION`/`LLM_UNAVAILABLE`/`NOT_ALLOWED`/409 各有中文引导；绝不自动重试。
 */
export function describeGenerateError(error: unknown): GenerateErrorInfo {
  if (error instanceof ApiClientError) {
    const detail = technicalDetail(error)
    const retryAfterSeconds = error.retryAfterSeconds
    if (error.status === 429 || error.code === 'OUT_OF_RANGE') {
      const wait = retryAfterSeconds > 0 ? `${retryAfterSeconds} 秒` : '一分钟'
      return {
        title: '生成太频繁',
        message: `生成太频繁了，请 ${wait}后重试（每分钟最多生成 5 次，不会自动重试）。`,
        detail,
        code: 'OUT_OF_RANGE',
        status: 429,
        retryAfterSeconds,
        clearKey: false,
      }
    }
    if (error.status === 409 || error.code === 'CONFLICT') {
      return {
        title: '请求内容变了',
        message: '这次生成的内容与上次不同，请重新点「生成人物卡」（系统已换用新的请求标识）。',
        detail,
        code: 'CONFLICT',
        status: 409,
        retryAfterSeconds: 0,
        clearKey: true,
      }
    }
    if (error.code === 'SCHEMA_VIOLATION') {
      return {
        title: '生成结果不完整',
        message:
          '生成的人物卡字段不完整或格式不对，请把 brief 写得更具体（来历、性格、擅长什么）后再试一次。',
        detail,
        code: 'SCHEMA_VIOLATION',
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    if (error.code === 'LLM_UNAVAILABLE') {
      return {
        title: '生成服务暂时不可用',
        message:
          '生成服务暂时不可用：稍后重试，或把 brief 写具体后继续使用模板草稿（默认离线时仍是模板草稿）。',
        detail,
        code: 'LLM_UNAVAILABLE',
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    if (error.status === 403 || error.code === 'NOT_ALLOWED') {
      return {
        title: '内容安全未通过',
        message: '内容安全审核未通过：请修改 brief 中可能违规的内容后再试。',
        detail,
        code: 'NOT_ALLOWED',
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    if (error.code === 'TIMEOUT') {
      return {
        title: '生成有点慢',
        message: '生成超时了，请稍后重试（不会自动重试）。',
        detail,
        code: 'TIMEOUT',
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    if (error.status === 422 || error.code === 'INVALID_INPUT') {
      return {
        title: '填写的内容不对',
        message: `请检查 brief 后再试：${error.message}`,
        detail,
        code: error.code,
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    return {
      title: '生成失败',
      message: '暂时没能生成人物卡，请稍后重试（不会自动重试）。',
      detail,
      code: error.code,
      status: error.status,
      retryAfterSeconds: 0,
      clearKey: false,
    }
  }
  if (error instanceof TypeError) {
    return {
      title: '连接不上服务器',
      message: '请确认后端已在运行，然后手动重试（不会自动重试）。',
      detail: error.message,
      code: 'NETWORK',
      status: 0,
      retryAfterSeconds: 0,
      clearKey: false,
    }
  }
  const detail = error instanceof Error ? error.message : String(error)
  return {
    title: '生成失败',
    message: '暂时没能生成人物卡，请稍后重试（不会自动重试）。',
    detail,
    code: 'INTERNAL',
    status: 0,
    retryAfterSeconds: 0,
    clearKey: false,
  }
}

/** 保存到卡库失败 → 玩家可读文案 + 是否清幂等键（409 才清）。 */
export function describeSaveCardError(error: unknown): GenerateErrorInfo {
  if (error instanceof ApiClientError) {
    const detail = technicalDetail(error)
    if (error.status === 409 || error.code === 'CONFLICT') {
      return {
        title: '保存内容变了',
        message: '这张卡和上次保存的内容不一样，请再点一次「保存到卡库」（系统已换用新的请求标识）。',
        detail,
        code: 'CONFLICT',
        status: 409,
        retryAfterSeconds: 0,
        clearKey: true,
      }
    }
    if (error.status === 403 || error.code === 'NOT_ALLOWED') {
      return {
        title: '内容安全未通过',
        message: '内容安全审核未通过：请修改名称/背景/外貌后再保存。',
        detail,
        code: 'NOT_ALLOWED',
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    if (error.status === 413) {
      return {
        title: '卡片太大了',
        message: '人物卡 JSON 超过 64KB 上限，请精简背景/外貌后再保存。',
        detail,
        code: error.code,
        status: 413,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    if (error.status === 422 || error.code === 'INVALID_INPUT') {
      return {
        title: '卡片还没通过校验',
        message: '人物卡没通过服务端校验：请点「重新校验」查看原因，修改后再保存。',
        detail,
        code: error.code,
        status: error.status,
        retryAfterSeconds: 0,
        clearKey: false,
      }
    }
    return {
      title: '保存失败',
      message: '暂时没能保存到卡库，请稍后手动重试（不会自动重试）。',
      detail,
      code: error.code,
      status: error.status,
      retryAfterSeconds: 0,
      clearKey: false,
    }
  }
  if (error instanceof TypeError) {
    return {
      title: '连接不上服务器',
      message: '保存失败：请确认后端已在运行，然后手动重试（不会自动重试）。',
      detail: error.message,
      code: 'NETWORK',
      status: 0,
      retryAfterSeconds: 0,
      clearKey: false,
    }
  }
  const detail = error instanceof Error ? error.message : String(error)
  return {
    title: '保存失败',
    message: '暂时没能保存到卡库，请稍后手动重试（不会自动重试）。',
    detail,
    code: 'INTERNAL',
    status: 0,
    retryAfterSeconds: 0,
    clearKey: false,
  }
}
