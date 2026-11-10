/**
 * 幂等键生命周期（第 23 章 §23.3、D-M6-6④）。
 *
 * - 每个「逻辑动作」（创建对局/提交回合）在用户点击时生成一次 UUID；
 * - 网络失败后的**手动**重试复用同一键（服务端同键同参回放首次响应）；
 * - `CONFLICT` 不是重试：先刷新 `version`，用户重新提交时用新键（`clear` 后再取）；
 * - 禁自动重试：本模块不包含任何定时器/自动重放逻辑。
 */

const PREFIX = 'idem:'

function storage(): Storage {
  return globalThis.sessionStorage
}

/** 生成 UUID；旧环境无 `crypto.randomUUID` 时退回 RFC 4122 v4 形式。 */
function newUuid(): string {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const rand = Math.floor(Math.random() * 16)
    const value = char === 'x' ? rand : (rand % 4) + 8
    return value.toString(16)
  })
}

function storageKey(actionKey: string): string {
  return `${PREFIX}${actionKey}`
}

/** 取当前逻辑动作的幂等键；无则生成并存入 sessionStorage。 */
export function getOrCreate(actionKey: string): string {
  const key = storageKey(actionKey)
  const existing = storage().getItem(key)
  if (existing !== null && existing !== '') {
    return existing
  }
  const created = newUuid()
  storage().setItem(key, created)
  return created
}

/** 成功或最终失败后清除；下次同动作会重新生成（新逻辑动作）。 */
export function clear(actionKey: string): void {
  storage().removeItem(storageKey(actionKey))
}

/** `CONFLICT` 后刷新版本再提交：立即换一个新键。 */
export function rotate(actionKey: string): string {
  clear(actionKey)
  return getOrCreate(actionKey)
}
