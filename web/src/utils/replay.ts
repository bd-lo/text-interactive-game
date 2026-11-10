/**
 * 回放纯函数（M6-027）：时间轴定位与叙事文本提取。
 *
 * 只做确定性计算，不触网、不写状态；可被 vitest 直接单测（第 23 章 §23.8 单元层）。
 */

import type { ReplayEvent } from '@/api/types'

/** 事件缺少 `result.narrative` 或类型不对时的兜底文案（不渲染原始 params/result）。 */
export const NARRATIVE_FALLBACK = '（该事件没有可回放的叙事文本）'

/** `result.narrative` 文本源（D-M6-3）；非字符串/空串回退兜底文案。 */
export function narrativeOf(event: ReplayEvent | null): string {
  if (event === null) {
    return NARRATIVE_FALLBACK
  }
  const value = event.result.narrative
  return typeof value === 'string' && value !== '' ? value : NARRATIVE_FALLBACK
}

/**
 * 把任意定位值 clamp 到 `[0, count-1]`。
 * - `count<=0` → `-1`（无事件）；
 * - 非有限数（如空 range 的 NaN）→ 0；
 * - 小数向下取整（拖动/点击取整）。
 */
export function clampIndex(index: number, count: number): number {
  if (count <= 0) {
    return -1
  }
  if (!Number.isFinite(index)) {
    return 0
  }
  return Math.max(0, Math.min(count - 1, Math.trunc(index)))
}

/** 相对定位（首/末/上一步/下一步）；越界时 clamp 到端点。 */
export function stepIndex(index: number, count: number, delta: number): number {
  if (count <= 0) {
    return -1
  }
  return clampIndex(clampIndex(index, count) + delta, count)
}

/** 全局 `seq` → 已加载事件下标（无匹配返回 -1）。 */
export function indexOfSeq(events: readonly ReplayEvent[], seq: number): number {
  return events.findIndex((event) => event.seq === seq)
}

/** 已加载事件的末 `seq`（空列表为 0）。 */
export function lastSeqOf(events: readonly ReplayEvent[]): number {
  if (events.length === 0) {
    return 0
  }
  return events[events.length - 1]?.seq ?? 0
}
