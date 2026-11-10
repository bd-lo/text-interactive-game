import { beforeEach, describe, expect, it } from 'vitest'

import { clear, getOrCreate, rotate } from '@/utils/idempotency'

describe('幂等键 helper（第 23 章 §23.3 / D-M6-6④）', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('同一逻辑动作复用同一 UUID', () => {
    const first = getOrCreate('turn:g1:1')
    const second = getOrCreate('turn:g1:1')
    expect(second).toBe(first)
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  })

  it('不同逻辑动作生成不同键', () => {
    expect(getOrCreate('turn:g1:1')).not.toBe(getOrCreate('turn:g1:2'))
  })

  it('clear 后重新生成新键（新逻辑动作 / CONFLICT 后）', () => {
    const first = getOrCreate('turn:g1:1')
    clear('turn:g1:1')
    const second = getOrCreate('turn:g1:1')
    expect(second).not.toBe(first)
  })

  it('rotate 立即换新键并保持稳定', () => {
    const first = getOrCreate('turn:g1:1')
    const rotated = rotate('turn:g1:1')
    expect(rotated).not.toBe(first)
    expect(getOrCreate('turn:g1:1')).toBe(rotated)
  })
})
