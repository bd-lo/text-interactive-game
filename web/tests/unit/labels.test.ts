import { describe, expect, it } from 'vitest'

import {
  formatEventRef,
  formatEventSeq,
  formatTurn,
  labelActor,
  labelEventType,
  labelGameStatus,
  labelMethod,
} from '@/utils/labels'

describe('回放元数据中文标签映射（未知值回退原值）', () => {
  it('event_type：已知映射中文、未知回退原值、null 占位', () => {
    expect(labelEventType('ITEM_TAKEN')).toBe('拾取物品')
    expect(labelEventType('game_created')).toBe('游戏创建')
    expect(labelEventType('CRAFT_DETERMINISTIC')).toBe('制造（确定性）')
    expect(labelEventType('FUTURE_EVENT_V2')).toBe('FUTURE_EVENT_V2')
    expect(labelEventType(null)).toBe('（无）')
    expect(labelEventType(undefined)).toBe('（无）')
    expect(labelEventType('')).toBe('（无）')
  })

  it('method：已知映射中文、未知回退原值', () => {
    expect(labelMethod('take_item')).toBe('拾取')
    expect(labelMethod('move_to')).toBe('移动')
    expect(labelMethod('craft_item')).toBe('制造')
    expect(labelMethod('future_method')).toBe('future_method')
  })

  it('actor_id：固定场景 ID/player/system → 中文，未知回退原值', () => {
    expect(labelActor('player')).toBe('玩家')
    expect(labelActor('char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN')).toBe('玩家')
    expect(labelActor('npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP')).toBe('老哨兵')
    expect(labelActor('char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV')).toBe('拾荒者甲')
    expect(labelActor('char_unknown_actor')).toBe('char_unknown_actor')
  })

  it('seq/turn 展示更可读；对局状态中文化', () => {
    expect(formatEventSeq(3)).toBe('事件 #3')
    expect(formatTurn(2)).toBe('第 2 回合')
    expect(formatEventRef(3, 2)).toBe('第 2 回合 · 事件 #3')
    expect(labelGameStatus('active')).toBe('进行中')
    expect(labelGameStatus('ended')).toBe('已结束')
  })
})
