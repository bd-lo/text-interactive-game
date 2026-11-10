import { describe, expect, it } from 'vitest'

import type { GamePlayerViewResponse, ReplayEvent } from '@/api/types'
import {
  appendNarrative,
  appendNotice,
  displayHistory,
  ENDING_ENTRY_ID,
  historyFromReplay,
  mergeReplayHistory,
  narrativeOfEvent,
  OPENING_ENTRY_ID,
  openingTextOf,
  withEnding,
  withOpening,
} from '@/utils/history'

function makeView(overrides: Partial<GamePlayerViewResponse> = {}): GamePlayerViewResponse {
  return {
    game_id: 'g1',
    turn: 1,
    version: 1,
    snapshot_version: 1,
    status: 'active',
    hp: 18,
    max_hp: 20,
    inventory: [],
    room: {
      room_id: 'room_entry',
      title: '哨站地窖',
      description: '墙上的信标还暗着。',
      exits: {},
      items: [],
      npcs: [],
    },
    goal: {
      title: '余烬哨站',
      description: '找到失踪的哨兵并点亮信标。',
      objectives: [
        { id: 'o1', text: '进入哨站', done: true },
        { id: 'o2', text: '找到老哨兵', done: false },
      ],
    },
    ending_text: null,
    ...overrides,
  }
}

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

  it('P2-2 withOpening：空历史插入「开场」= 房间描述 + 目标引导；事件回合后不插入', () => {
    const view = makeView()
    const opened = withOpening([], view)

    expect(opened).toHaveLength(1)
    expect(opened[0]).toMatchObject({ id: OPENING_ENTRY_ID, kind: 'opening', turnId: null })
    expect(opened[0]?.text).toContain('墙上的信标还暗着。')
    expect(opened[0]?.text).toContain('目标：余烬哨站（进度 1/2）')

    const existing = [{ id: 'turn:2', kind: 'narrative' as const, text: '已有一回合', turnId: 2 }]
    expect(withOpening(existing, view)).toEqual(existing)
    expect(withOpening([], null)).toEqual([])

    // 无房间描述不伪造开场；无 goal 时只显示描述
    expect(withOpening([], makeView({ room: { ...view.room, description: '   ' } }))).toEqual([])
    expect(openingTextOf(makeView({ goal: null }))).toBe('墙上的信标还暗着。')
  })

  it('H4：新局首个只读回合（turnId=1，零事件）仍保留「开场」，记录可见地 +1 行', () => {
    const view = makeView()
    const readonlyTurn = [
      { id: 'local:1', kind: 'narrative' as const, text: '你环顾四周，把入口看了一遍。', turnId: 1 },
    ]

    const displayed = withOpening(readonlyTurn, view)

    expect(displayed).toHaveLength(2)
    expect(displayed[0]).toMatchObject({ id: OPENING_ENTRY_ID, kind: 'opening' })
    expect(displayed[1]?.text).toContain('你环顾四周')
    // 只读回合后不再是「纯空局」，但还未推进回合 → 保留开场（e2e s2.badge.no-event 回归点）
    expect(withOpening([...readonlyTurn, { id: 'notice:1', kind: 'notice' as const, text: '（未生效）', turnId: null }], view)).toHaveLength(2)
  })

  it('AC-10d withEnding：ended 才追加固定 id「结局」行，重复派生幂等', () => {
    const base = [{ id: 'turn:2', kind: 'narrative' as const, text: '你点亮了信标。', turnId: 2 }]
    const ended = withEnding(base, { status: 'ended', endingText: ' 信标在风里亮起。 ' })

    expect(ended).toHaveLength(2)
    expect(ended[1]).toMatchObject({ id: ENDING_ENTRY_ID, kind: 'ending', turnId: null })
    expect(ended[1]?.text).toBe('信标在风里亮起。')
    // 重复计算不重复追加
    expect(withEnding(ended, { status: 'ended', endingText: '信标在风里亮起。' })).toEqual(ended)
    // 未结束 / 空结局文本：不改日志
    expect(withEnding(base, { status: 'active', endingText: '信标在风里亮起。' })).toEqual(base)
    expect(withEnding(base, { status: 'ended', endingText: '   ' })).toEqual(base)
  })

  it('displayHistory：开场在前、结局在后、重复计算幂等', () => {
    const endedView = makeView({ status: 'ended', ending_text: '信标在风里亮起。' })
    const first = displayHistory([], {
      view: endedView,
      status: 'ended',
      endingText: endedView.ending_text,
    })

    expect(first.map((entry) => entry.kind)).toEqual(['opening', 'ending'])
    const second = displayHistory(first, {
      view: endedView,
      status: 'ended',
      endingText: endedView.ending_text,
    })
    expect(second).toEqual(first)
  })
})
