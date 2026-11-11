/**
 * LLM 设置前端工具：来源/状态中文标签、连接测试文案、API Key 输入预检、
 * 首屏「未配置」横幅的关闭状态（localStorage，隐私模式下静默降级）。
 *
 * 口径：字段级校验一律以服务端 `PUT/POST /api/v1/settings/llm*` 为准；
 * 这里只做展示文案与「空 key 不必发请求」的体验优化；明文 key 不写任何存储。
 */

import type { LlmConnectionTest, LlmSettingsStatus } from '@/api/types'

/** 首屏横幅关闭标记（localStorage；升级文案结构时改版本后缀）。 */
export const LLM_BANNER_DISMISSED_KEY = 'textgame:llm-banner-dismissed'

/** 表单默认值（与后端 `LlmSaveRequestBody` 默认一致）。 */
export const LLM_DEFAULT_PROVIDER = 'deepseek'
export const LLM_DEFAULT_MODEL = 'deepseek-flash'
export const LLM_DEFAULT_API_BASE = 'https://api.deepseek.com'

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function storage(): StorageLike | null {
  try {
    return globalThis.localStorage
  } catch {
    return null
  }
}

/** 配置来源 → 玩家可读标签（未知值回退原值，便于发现后端新增来源）。 */
export function labelLlmSource(source: string): string {
  if (source === 'env') {
    return '开发环境 .env'
  }
  if (source === 'user') {
    return '本机保存'
  }
  if (source === 'none') {
    return '未配置'
  }
  return source
}

/** 是否已启用 AI → 中文。 */
export function labelLlmEnabled(enabled: boolean): string {
  return enabled ? '已启用' : '未启用（离线模板叙事）'
}

/** 打码 key 展示：未保存时给占位。 */
export function formatMaskedKey(maskedKey: string | null): string {
  return maskedKey === null || maskedKey === '' ? '（未保存）' : maskedKey
}

/** 连接测试结果 → 一行中文（成功带延迟；失败带人话原因）。 */
export function describeLlmTest(test: LlmConnectionTest): string {
  if (test.ok) {
    const latency = test.latency_ms === null ? '' : ` · 延迟 ${test.latency_ms} 毫秒`
    return `连接成功（${test.provider} / ${test.model}${latency}）`
  }
  const reason = test.error === null || test.error.trim() === '' ? '未返回具体原因' : test.error
  return `连接失败（${test.provider} / ${test.model}）：${reason}`
}

/** API Key 输入预检：去掉首尾空白后不能为空（保存时必须重新输入完整 key）。 */
export function checkApiKeyInput(value: string): { ok: boolean; error: string } {
  if (value.trim() === '') {
    return {
      ok: false,
      error: '请先填 DeepSeek API Key（sk-...）；出于安全，本页不回显已保存的 key。',
    }
  }
  return { ok: true, error: '' }
}

/** 首屏「未配置」横幅是否显示：未配置且未启用、且用户没手动关过。 */
export function shouldShowLlmBanner(
  status: LlmSettingsStatus | null,
  dismissed: boolean,
): boolean {
  return status !== null && !status.configured && !status.enabled && !dismissed
}

/** 读取「横幅已关闭」标记（存储不可用 → false）。 */
export function isLlmBannerDismissed(store: StorageLike | null = storage()): boolean {
  if (store === null) {
    return false
  }
  try {
    return store.getItem(LLM_BANNER_DISMISSED_KEY) === '1'
  } catch {
    return false
  }
}

/** 记住「横幅已关闭」（存储不可用静默降级，不阻塞首屏）。 */
export function dismissLlmBanner(store: StorageLike | null = storage()): void {
  if (store === null) {
    return
  }
  try {
    store.setItem(LLM_BANNER_DISMISSED_KEY, '1')
  } catch {
    // 隐私模式/配额满：仅本次会话生效
  }
}
