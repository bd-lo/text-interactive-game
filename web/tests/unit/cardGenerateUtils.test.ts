import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClientError } from '@/api/client'
import type { CharacterCard, CharacterCardWarning } from '@/api/types'
import {
  countBriefChars,
  describeGenerateError,
  describeGenerateWarning,
  describeSaveCardError,
  draftBadgeLabel,
  formatCardValidationError,
  GENERATE_BRIEF_MAX_CHARS,
  hasTemplateFallbackWarning,
  isBriefValid,
  isTemplateFallback,
  prettyCardJson,
} from '@/utils/cardGenerate'

const CARD: CharacterCard = {
  schema_version: '1.0',
  name: '灰烬旅人',
  attributes: { str: 12, dex: 14, con: 11, int: 15, wis: 10, cha: 8, max_hp: 30 },
  hp: 30,
  starting_items: ['item.torch'],
  background: '在旧矿镇长大的拾荒者。',
  appearance: null,
  source: 'manual',
  generated_by: null,
}

const FALLBACK_WARNING: CharacterCardWarning = {
  code: 'TEMPLATE_FALLBACK',
  message: 'LLM 不可用；已使用确定性模板草稿',
  field: null,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('生成人物卡工具函数（M6-028）', () => {
  it('brief 按码点计数：emoji 代理对算 1 个字，上限 1000', () => {
    expect(countBriefChars('灰烬旅人')).toBe(4)
    expect(countBriefChars('🎲')).toBe(1)
    expect(countBriefChars('🎲'.repeat(1000))).toBe(1000)
    expect(isBriefValid('   灰烬旅人   ')).toBe(true)
    expect(isBriefValid('   ')).toBe(false)
    expect(isBriefValid('a'.repeat(GENERATE_BRIEF_MAX_CHARS))).toBe(true)
    expect(isBriefValid('a'.repeat(GENERATE_BRIEF_MAX_CHARS + 1))).toBe(false)
    // 码点口径：1000 个 emoji 合法（UTF-16 length 是 2000）
    expect(isBriefValid('🎲'.repeat(1000))).toBe(true)
  })

  it('模板兜底判定：generated_by=null 或 warnings 含 TEMPLATE_FALLBACK（容错任一成立）', () => {
    expect(hasTemplateFallbackWarning([FALLBACK_WARNING])).toBe(true)
    expect(hasTemplateFallbackWarning([])).toBe(false)
    expect(hasTemplateFallbackWarning(undefined)).toBe(false)
    expect(isTemplateFallback(null, [FALLBACK_WARNING])).toBe(true)
    expect(isTemplateFallback(null, [])).toBe(true)
    expect(isTemplateFallback('deepseek:chat', [FALLBACK_WARNING])).toBe(true)
    expect(isTemplateFallback('deepseek:chat', [])).toBe(false)
    expect(draftBadgeLabel(true)).toBe('模板草稿')
    expect(draftBadgeLabel(false)).toBe('LLM 草稿')
  })

  it('生成告警中文化：TEMPLATE_FALLBACK 固定中文，不暴露英文错误码', () => {
    const text = describeGenerateWarning(FALLBACK_WARNING)
    expect(text).toContain('模板草稿')
    expect(text).not.toContain('TEMPLATE_FALLBACK')
    expect(
      describeGenerateWarning({ code: 'DUPLICATE_ITEM_MERGED', message: 'duplicate merged' }),
    ).toContain('合并')
    expect(describeGenerateWarning({ code: 'OTHER', message: '自定义提示' })).toBe('自定义提示')
  })

  it('校验错误项格式化：字段路径 + 说明（空路径回退「根」）', () => {
    expect(
      formatCardValidationError({ loc: ['hp'], type: 'value_error', msg: 'hp 不能超过上限' }),
    ).toBe('hp：hp 不能超过上限')
    expect(formatCardValidationError({ loc: [], type: 'x', msg: '根对象错误' })).toBe(
      '（根）：根对象错误',
    )
  })

  it('prettyCardJson 输出可再次解析的 9 字段 JSON', () => {
    const text = prettyCardJson(CARD)
    expect(JSON.parse(text)).toEqual(CARD)
    expect(text).toContain('\n  "name": "灰烬旅人"')
  })

  it('429 错误映射：提示 Retry-After 秒数、无自动重试、不清幂等键', () => {
    const info = describeGenerateError(new ApiClientError(429, 'OUT_OF_RANGE', '过于频繁', undefined, 37))
    expect(info.title).toBe('生成太频繁')
    expect(info.message).toContain('37 秒后重试')
    expect(info.message).toContain('不会自动重试')
    expect(info.retryAfterSeconds).toBe(37)
    expect(info.clearKey).toBe(false)
    expect(info.detail).toContain('HTTP 429')
  })

  it('SCHEMA_VIOLATION / LLM_UNAVAILABLE / NOT_ALLOWED 各有中文引导', () => {
    const schema = describeGenerateError(new ApiClientError(422, 'SCHEMA_VIOLATION', 'schema bad'))
    expect(schema.title).toBe('生成结果不完整')
    expect(schema.message).toContain('brief')

    const llm = describeGenerateError(new ApiClientError(503, 'LLM_UNAVAILABLE', 'llm down'))
    expect(llm.title).toBe('生成服务暂时不可用')
    expect(llm.message).toContain('模板草稿')

    const blocked = describeGenerateError(new ApiClientError(403, 'NOT_ALLOWED', 'blocked'))
    expect(blocked.title).toBe('内容安全未通过')
    expect(blocked.message).toContain('brief')
  })

  it('409 映射为「请求内容变了」并要求清幂等键；网络错误给出中文提示', () => {
    const conflict = describeGenerateError(new ApiClientError(409, 'CONFLICT', 'mismatch'))
    expect(conflict.title).toBe('请求内容变了')
    expect(conflict.clearKey).toBe(true)

    const network = describeGenerateError(new TypeError('Failed to fetch'))
    expect(network.title).toBe('连接不上服务器')
    expect(network.message).toContain('不会自动重试')
    expect(network.clearKey).toBe(false)
  })

  it('保存失败映射：409 清键 / 422 引导重新校验 / 403 内容安全', () => {
    const conflict = describeSaveCardError(new ApiClientError(409, 'CONFLICT', 'mismatch'))
    expect(conflict.clearKey).toBe(true)
    expect(conflict.message).toContain('保存到卡库')

    const invalid = describeSaveCardError(new ApiClientError(422, 'INVALID_INPUT', 'bad'))
    expect(invalid.message).toContain('重新校验')

    const blocked = describeSaveCardError(new ApiClientError(403, 'NOT_ALLOWED', 'blocked'))
    expect(blocked.title).toBe('内容安全未通过')
  })
})
