import { describe, expect, it } from 'vitest'

import { ApiClientError } from '@/api/client'
import {
  WORLD_BOOK_MAX_BYTES,
  checkWorldBookFileSize,
  checkWorldBookText,
  describeWorldBookError,
  labelDetectedFormat,
  labelSelectiveLogic,
  labelWorldBookSource,
} from '@/utils/worldBooks'

describe('世界书前端工具（预检/文案）', () => {
  it('checkWorldBookText：空/语法错/根非对象被拦，合法对象通过', () => {
    expect(checkWorldBookText('').ok).toBe(false)
    expect(checkWorldBookText('{broken').error).toContain('JSON 语法错误')
    expect(checkWorldBookText('[1,2]').error).toContain('根节点必须是对象')

    const ok = checkWorldBookText('{"entries": []}')
    expect(ok.ok).toBe(true)
    expect(ok.bytes).toBeGreaterThan(0)
  })

  it('checkWorldBookText：按 UTF-8 字节判断 256KB 上限（中文 3 字节/字）', () => {
    const big = JSON.stringify({ content: '世'.repeat(120_000) })
    const result = checkWorldBookText(big)
    expect(result.ok).toBe(false)
    expect(result.error).toContain('256KB')
    expect(result.bytes).toBeGreaterThan(WORLD_BOOK_MAX_BYTES)
  })

  it('checkWorldBookFileSize：≤256KB 通过，>256KB 拒绝', () => {
    expect(checkWorldBookFileSize(WORLD_BOOK_MAX_BYTES)).toBe('')
    expect(checkWorldBookFileSize(WORLD_BOOK_MAX_BYTES + 1)).toContain('文件过大')
  })

  it('来源/格式/触发逻辑文案可读，未知值回退原值', () => {
    expect(labelWorldBookSource('tavern')).toBe('酒馆格式')
    expect(labelWorldBookSource('character_card')).toBe('角色卡内嵌')
    expect(labelWorldBookSource('manual')).toBe('简化格式')
    expect(labelWorldBookSource('future_source')).toBe('future_source')
    expect(labelDetectedFormat('tavern')).toBe('酒馆格式')
    expect(labelDetectedFormat(undefined)).toBe('未知格式')
    expect(labelSelectiveLogic(0)).toContain('任一命中')
    expect(labelSelectiveLogic(1)).toContain('NOT_ALL')
    expect(labelSelectiveLogic(2)).toContain('NOT_ANY')
    expect(labelSelectiveLogic(3)).toContain('AND_ALL')
  })

  it('describeWorldBookError：413 给「文件太大」人话，其余复用统一映射', () => {
    const tooLarge = describeWorldBookError(
      new ApiClientError(413, 'INVALID_INPUT', '世界书 JSON 超过 256KB 上限'),
    )
    expect(tooLarge.title).toContain('文件')
    expect(tooLarge.message).toContain('256KB')

    const notFound = describeWorldBookError(
      new ApiClientError(404, 'NOT_FOUND', '世界书不存在: wb_x'),
    )
    expect(notFound.title).toContain('找不到')
  })
})
