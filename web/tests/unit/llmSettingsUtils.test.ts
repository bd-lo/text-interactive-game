import { afterEach, describe, expect, it } from 'vitest'

import type { LlmConnectionTest, LlmSettingsStatus } from '@/api/types'
import {
  checkApiKeyInput,
  describeLlmTest,
  dismissLlmBanner,
  formatMaskedKey,
  isLlmBannerDismissed,
  labelLlmEnabled,
  labelLlmSource,
  LLM_BANNER_DISMISSED_KEY,
  LLM_DEFAULT_API_BASE,
  LLM_DEFAULT_MODEL,
  shouldShowLlmBanner,
} from '@/utils/llm'

function status(overrides: Partial<LlmSettingsStatus> = {}): LlmSettingsStatus {
  return {
    configured: false,
    enabled: false,
    source: 'none',
    provider: 'deepseek',
    model: 'deepseek-flash',
    api_base: 'https://api.deepseek.com',
    masked_key: null,
    has_saved_user_key: false,
    detail: '未配置',
    ...overrides,
  }
}

function testResult(overrides: Partial<LlmConnectionTest> = {}): LlmConnectionTest {
  return {
    ok: true,
    latency_ms: 250,
    provider: 'deepseek',
    model: 'deepseek-flash',
    error: null,
    ...overrides,
  }
}

afterEach(() => {
  localStorage.clear()
})

describe('LLM 设置展示辅助', () => {
  it('来源标签：env/user/none → 中文，未知值回退原值', () => {
    expect(labelLlmSource('env')).toBe('开发环境 .env')
    expect(labelLlmSource('user')).toBe('本机保存')
    expect(labelLlmSource('none')).toBe('未配置')
    expect(labelLlmSource('proxy')).toBe('proxy')
  })

  it('启用状态 / 打码 key / 默认值常量', () => {
    expect(labelLlmEnabled(true)).toBe('已启用')
    expect(labelLlmEnabled(false)).toContain('未启用')
    expect(formatMaskedKey(null)).toBe('（未保存）')
    expect(formatMaskedKey('')).toBe('（未保存）')
    expect(formatMaskedKey('sk-****abcd')).toBe('sk-****abcd')
    expect(LLM_DEFAULT_MODEL).toBe('deepseek-flash')
    expect(LLM_DEFAULT_API_BASE).toBe('https://api.deepseek.com')
  })

  it('连接测试文案：成功带延迟；失败带人话原因，缺原因时兜底', () => {
    expect(describeLlmTest(testResult())).toBe('连接成功（deepseek / deepseek-flash · 延迟 250 毫秒）')
    expect(describeLlmTest(testResult({ latency_ms: null }))).toBe(
      '连接成功（deepseek / deepseek-flash）',
    )
    expect(describeLlmTest(testResult({ ok: false, latency_ms: null, error: '鉴权失败（401）' }))).toBe(
      '连接失败（deepseek / deepseek-flash）：鉴权失败（401）',
    )
    expect(describeLlmTest(testResult({ ok: false, latency_ms: null, error: null }))).toContain(
      '未返回具体原因',
    )
  })

  it('API Key 输入预检：空/纯空白拦下，非空通过', () => {
    expect(checkApiKeyInput('').ok).toBe(false)
    expect(checkApiKeyInput('   ').error).toContain('API Key')
    expect(checkApiKeyInput(' sk-abc ')).toEqual({ ok: true, error: '' })
  })

  it('首屏横幅显隐矩阵：未配置且未启用且未关闭才显示', () => {
    expect(shouldShowLlmBanner(null, false)).toBe(false)
    expect(shouldShowLlmBanner(status(), false)).toBe(true)
    expect(shouldShowLlmBanner(status(), true)).toBe(false)
    expect(shouldShowLlmBanner(status({ configured: true }), false)).toBe(false)
    expect(shouldShowLlmBanner(status({ enabled: true }), false)).toBe(false)
    expect(
      shouldShowLlmBanner(
        status({ configured: true, enabled: true, source: 'user', masked_key: 'sk-****1234' }),
        false,
      ),
    ).toBe(false)
  })

  it('横幅关闭状态：localStorage 读写 + 存储不可用静默降级', () => {
    expect(isLlmBannerDismissed()).toBe(false)
    dismissLlmBanner()
    expect(localStorage.getItem(LLM_BANNER_DISMISSED_KEY)).toBe('1')
    expect(isLlmBannerDismissed()).toBe(true)

    expect(isLlmBannerDismissed(null)).toBe(false)
    expect(() => dismissLlmBanner(null)).not.toThrow()
    const throwing = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
      removeItem: () => undefined,
    }
    expect(isLlmBannerDismissed(throwing)).toBe(false)
    expect(() => dismissLlmBanner(throwing)).not.toThrow()
  })
})
