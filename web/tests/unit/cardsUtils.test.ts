import { describe, expect, it } from 'vitest'

import { ApiClientError } from '@/api/client'
import {
  CARD_MAX_BYTES,
  checkCardFileSize,
  checkCardText,
  describeCardError,
  formatViolations,
} from '@/utils/cards'

describe('卡库导入客户端预检（不引 Ajv，服务端 validate 为准）', () => {
  it('checkCardText：合法对象通过、空/语法错/根非对象被拦', () => {
    expect(checkCardText('{"name":"灰烬旅人"}').ok).toBe(true)
    expect(checkCardText('   ').error).toContain('为空')
    expect(checkCardText('{oops}').error).toContain('JSON 语法错误')
    expect(checkCardText('[1,2,3]').error).toContain('根节点必须是对象')
    expect(checkCardText('null').error).toContain('根节点必须是对象')
  })

  it('checkCardText 按 UTF-8 字节判断 >64KB（中文 3 字节/字）', () => {
    const within = JSON.stringify({ name: '灰'.repeat(1000), background: 'x'.repeat(60000) })
    expect(checkCardText(within).bytes).toBeLessThanOrEqual(CARD_MAX_BYTES)
    expect(checkCardText(within).ok).toBe(true)

    const oversized = JSON.stringify({ background: 'x'.repeat(CARD_MAX_BYTES) })
    expect(checkCardText(oversized).ok).toBe(false)
    expect(checkCardText(oversized).error).toContain('64KB')
  })

  it('checkCardFileSize：≤64KB 通过，>64KB 提示文件过大', () => {
    expect(checkCardFileSize(CARD_MAX_BYTES)).toBe('')
    expect(checkCardFileSize(CARD_MAX_BYTES + 1)).toContain('文件过大')
  })

  it('describeCardError：413/422/403/409/404 映射为可读中文提示', () => {
    expect(describeCardError(new ApiClientError(413, 'INVALID_INPUT', 'too large')).message).toContain('413')
    expect(describeCardError(new ApiClientError(422, 'SCHEMA_VIOLATION', 'bad')).message).toContain('422')
    expect(describeCardError(new ApiClientError(403, 'NOT_ALLOWED', 'xss')).message).toContain('内容安全')
    expect(describeCardError(new ApiClientError(409, 'CONFLICT', 'idem')).message).toContain('幂等冲突')
    expect(describeCardError(new ApiClientError(404, 'NOT_FOUND', 'nope')).message).toContain('404')
    expect(describeCardError(new Error('network down')).message).toBe('network down')
  })

  it('describeCardError：角色卡 V2 包装被卡库拒绝时引导到世界书导入（H4 WB-03b）', () => {
    const info = describeCardError(
      new ApiClientError(422, 'SCHEMA_VIOLATION', 'character card schema violation', [
        { loc: ['body', 'spec'], type: 'extra_forbidden', msg: 'Extra inputs are not permitted' },
        { loc: ['body', 'data', 'character_book'], type: 'extra_forbidden', msg: 'Extra inputs are not permitted' },
      ]),
    )
    expect(info.status).toBe(422)
    expect(info.message).toContain('世界书文件')
    expect(info.message).toContain('导入世界书')
    expect(info.message).toContain('9 字段')
  })

  it('describeCardError 404：玩家可见文案不转发服务端 message 里的内部 card ID（AC-23）', () => {
    const info = describeCardError(
      new ApiClientError(
        404,
        'NOT_FOUND',
        '人物卡不存在: card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
      ),
    )
    expect(info.status).toBe(404)
    expect(info.message).toContain('可能已被删除')
    expect(info.message).not.toMatch(/card_[0-9A-Z]{8,}/)
  })

  it('formatViolations：把 [{loc,type,msg}] 转成 «loc：msg» 行', () => {
    expect(
      formatViolations([
        { loc: ['hp'], type: 'value_error', msg: 'hp must be <= attributes.max_hp' },
        { loc: [], type: 'value_error', msg: 'card must be a JSON object' },
        'garbage',
      ]),
    ).toEqual(['hp：hp must be <= attributes.max_hp', 'card must be a JSON object'])
    expect(formatViolations(undefined)).toEqual([])
  })
})
