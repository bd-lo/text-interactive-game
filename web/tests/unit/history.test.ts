import { describe, expect, it } from 'vitest'

import type { ReplayEvent } from '@/api/types'
import {
  appendNarrative,
  appendNotice,
  historyFromReplay,
  mergeReplayHistory,
  narrativeOfEvent,
} from '@/utils/history'

function event(overrides: Partial<ReplayEvent> & { seq: number; turn_id: number }): ReplayEvent {
  const base: ReplayEvent = {
    id: `evt-${overrides.seq}`,
    seq: overrides.seq,
    turn_id: overrides.turn_id,
    actor_id: 'char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN',
    method: 'move_to',
    event_type: 'CHARACTER_MOVED',
    params: {},
    result: {},
    rng_seed: 42,
    ts: '2026-11-10T15:00:00Z',
  }
  return { ...base, ...overrides }
}

describe('冒险记录纯函数（replay → 回合日志）', () => {
  it('narrativeOfEvent：只认非空字符串叙事', () => {
    expect(narrativeOfEvent(event({ seq: 1, turn_id: 2, result: { narrative: ' 你走进地窖。 ' } }))).toBe(
      '你走进地窖。',
    )
    expect(narrativeOfEvent(event({ seq: 1, turn_id: 2, result: {} }))).toBe('')
    expect(narrativeOfEvent(event({ seq: 1, turn_id: 2, result: { narrative: 42 } }))).toBe('')
    expect(narrativeOfEvent(event({ seq: 1, turn_id: 2, result: { narrative: '   ' } }))).toBe('')
  })

  it('historyFromReplay：按 seq 升序、每回合一条、过滤无叙事事件', () => {
    const history = historyFromReplay([
      event({ seq: 5, turn_id: 4, result: { narrative: '你继续向北。' } }),
      event({ seq: 1, turn_id: 1, result: {} }), // game_created 无叙事
      event({ seq: 2, turn_id: 2, result: { narrative: '你捡起了火把。' } }),
      // m3 实测：同回合玩家/NPC 事件写入同一段叙事 → 只保留一条
      event({ seq: 3, turn_id: 3, result: { narrative: '你向北动身。' } }),
      event({ seq: 4, turn_id: 3, actor_id: 'npc_x', result: { narrative: '你向北动身。' } }),
    ])

    expect(history.map((entry) => entry.turnId)).toEqual([2, 3, 4])
    expect(history.map((entry) => entry.text)).toEqual([
      '你捡起了火把。',
      '你向北动身。',
      '你继续向北。',
    ])
    expect(history.every((entry) => entry.kind === 'narrative')).toBe(true)
    expect(history[0]?.id).toBe('turn:2')
  })

  it('appendNarrative：追加本会话回合，空文本忽略，id 稳定唯一', () => {
    const first = appendNarrative([], { text: '你拿起火把。', turnId: 2 })
    const second = appendNarrative(first, { text: '你向北走。', turnId: 3 })
    const ignored = appendNarrative(second, { text: '   ', turnId: 4 })

    expect(ignored).toHaveLength(2)
    expect(new Set(ignored.map((entry) => entry.id)).size).toBe(2)
    expect(ignored[1]?.text).toBe('你向北走。')
  })

  it('appendNotice：失败回合追加系统行，不改写已有叙事', () => {
    const history = appendNotice(appendNarrative([], { text: '你拿起火把。', turnId: 2 }))
    expect(history).toHaveLength(2)
    expect(history[1]?.kind).toBe('notice')
    expect(history[1]?.text).toBe('（该指令未生效）')
    expect(history[0]?.text).toBe('你拿起火把。')
  })

  it('mergeReplayHistory：用 replay 重建叙事并保留系统行', () => {
    const current = appendNotice(
      appendNarrative([], { text: '本会话旧叙事', turnId: 2 }),
      '（网络异常，指令状态未知）',
    )
    const rebuilt = [{ id: 'turn:9', kind: 'narrative' as const, text: '重建的叙事', turnId: 9 }]

    const merged = mergeReplayHistory(rebuilt, current)
    expect(merged.map((entry) => entry.kind)).toEqual(['narrative', 'notice'])
    expect(merged[0]?.text).toBe('重建的叙事')
    expect(merged[1]?.text).toBe('（网络异常，指令状态未知）')
  })
})
