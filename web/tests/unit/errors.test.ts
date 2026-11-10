import { describe, expect, it } from 'vitest'

import { ApiClientError } from '@/api/client'
import {
  describeError,
  describeTurnFailure,
  describeViolations,
  diagnosticOf,
} from '@/utils/errors'

describe('玩家可读错误文案（错误码 → 人话，技术细节只进诊断区）', () => {
  it('describeViolations：loc/type 中文映射（422 校验项可读）', () => {
    const lines = describeViolations([
      { loc: ['body', 'seed'], type: 'int_parsing', msg: 'Input should be a valid integer' },
      { loc: ['body', 'scenario'], type: 'literal_error', msg: 'Input should be ...' },
      { loc: [], type: 'weird', msg: '未知原因' },
      'not-an-object',
    ])
    expect(lines).toEqual(['随机种子：需要是整数', '场景：取值不在允许范围', '未知原因'])
  })

  it('ApiClientError：404/422/TIMEOUT 都给人话标题，detail 保留 error_code', () => {
    const notFound = describeError(new ApiClientError(404, 'NOT_FOUND', '存档不存在: x'))
    expect(notFound.title).toContain('找不到')
    expect(notFound.message).toContain('ID')
    expect(notFound.detail).toContain('NOT_FOUND')

    const invalid = describeError(
      new ApiClientError(422, 'INVALID_INPUT', '请求参数校验失败', [
        { loc: ['body', 'seed'], type: 'int_parsing', msg: 'x' },
      ]),
    )
    expect(invalid.title).toBe('填写的内容不对')
    expect(invalid.message).toContain('随机种子')
    expect(invalid.detail).toContain('INVALID_INPUT')

    const timeout = describeError(new ApiClientError(0, 'TIMEOUT', '请求超时或已取消，请重试'))
    expect(timeout.title).toContain('网络')
    expect(timeout.message).toContain('超时')
  })

  it('网络 TypeError：提示检查后端，而不是甩英文堆栈', () => {
    const info = describeError(new TypeError('fetch failed'))
    expect(info.title).toContain('连接不上')
    expect(info.message).toContain('后端')
    expect(diagnosticOf(info)).toBe('fetch failed')
  })

  it('describeTurnFailure：INVALID_INPUT 不暴露错误码前缀，CONFLICT 提示已同步', () => {
    const invalid = describeTurnFailure('INVALID_INPUT', '这个动作没有生效，你停下了脚步。')
    expect(invalid.title).toBe('这句指令没听懂')
    expect(invalid.message).toContain('这个动作没有生效')
    expect(invalid.detail).toContain('INVALID_INPUT')

    const conflict = describeTurnFailure('CONFLICT', '你的对局版本已经过期')
    expect(conflict.title).toContain('别处')
    expect(conflict.message).toContain('重新发送')
  })
})
