/**
 * 失败提示分组（M6 前端打磨）：`TurnResult.effects[].hint` + `suggested_actions`。
 *
 * 只读展示层：不拼进 `TurnResult.text`、不改原文展示（第 23 章 §23.3）；
 * 空值/非字符串/重复项一律过滤，避免出现空白分组。
 */

export interface TurnFeedback {
  /** `effects[].hint`（去空、去重）。 */
  hints: string[]
  /** `suggested_actions`（去空、去重）。 */
  actions: string[]
}

function normalize(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** 从 TurnResult 的加法字段收集「提示 + 可试试」两组文本。 */
export function collectTurnFeedback(
  effects: readonly Record<string, unknown>[],
  suggestedActions: readonly string[],
): TurnFeedback {
  const hints: string[] = []
  for (const effect of effects) {
    const hint = normalize(effect.hint)
    if (hint !== '' && !hints.includes(hint)) {
      hints.push(hint)
    }
  }
  const actions: string[] = []
  for (const action of suggestedActions) {
    const value = normalize(action)
    if (value !== '' && !actions.includes(value)) {
      actions.push(value)
    }
  }
  return { hints, actions }
}

/** 是否有可展示的反馈（两组都空则不显示组件）。 */
export function hasTurnFeedback(feedback: TurnFeedback): boolean {
  return feedback.hints.length > 0 || feedback.actions.length > 0
}
