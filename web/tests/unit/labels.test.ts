import { describe, expect, it } from 'vitest'

import {
  directionLabel,
  formatEndingSummary,
  formatEventRef,
  formatEventSeq,
  formatTurn,
  labelActor,
  labelCardSource,
  labelEventType,
  labelGameStatus,
  labelItemTemplate,
  labelItemTemplates,
  labelMethod,
  labelScenario,
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

  it('actor_id：固定场景 ID/player/system → 中文，未知内部 ULID → 「其他角色」不露 ID', () => {
    expect(labelActor('player')).toBe('玩家')
    expect(labelActor('char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN')).toBe('玩家')
    expect(labelActor('npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP')).toBe('老哨兵')
    expect(labelActor('char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV')).toBe('拾荒者甲')
    // 未登记的内部 ULID（如 story 的 NPC/新角色）→ 中性「其他角色」，绝不原样展示
    expect(labelActor('npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHKF')).toBe('其他角色')
    expect(labelActor('char_01J9Z3K4Q7X8Y9Z0ABCDEFGHZZ')).toBe('其他角色')
    // 非 ULID 的未知值回退原值（便于发现新增枚举，不误伤普通字符串）
    expect(labelActor('char_unknown_actor')).toBe('char_unknown_actor')
    expect(labelActor('future_actor')).toBe('future_actor')
  })

  it('seq/turn 展示更可读；对局状态中文化', () => {
    expect(formatEventSeq(3)).toBe('事件 #3')
    expect(formatTurn(2)).toBe('第 2 回合')
    expect(formatEventRef(3, 2)).toBe('第 2 回合 · 事件 #3')
    expect(labelGameStatus('active')).toBe('进行中')
    expect(labelGameStatus('ended')).toBe('已结束')
  })

  it('方位/场景标签中文映射，未知值回退原值', () => {
    expect(directionLabel('north')).toBe('北')
    expect(directionLabel('southwest')).toBe('西南')
    expect(directionLabel('portal')).toBe('portal')
    expect(labelScenario('m2')).toBe('废弃哨站（入门）')
    expect(labelScenario('m3')).toBe('拾荒者营地（多角色）')
    expect(labelScenario('story')).toBe('余烬哨站（剧情）')
    expect(labelScenario('future_scenario')).toBe('future_scenario')
  })

  it('P1 物品模板键 → 中文名（item. 与 item_ 两种写法等价，未知回退原键）', () => {
    expect(labelItemTemplate('item.torch')).toBe('火把')
    expect(labelItemTemplate('item_potion')).toBe('治疗药水')
    expect(labelItemTemplate('item.rusty_knife')).toBe('锈刀')
    expect(labelItemTemplate('item.copper_wire')).toBe('铜线')
    expect(labelItemTemplates(['item.torch', 'item.potion'])).toEqual(['火把', '治疗药水'])
    // 未知自定义模板键/实例 ID：不编造中文，回退原值（便于发现新模板）
    expect(labelItemTemplate('item.custom_future')).toBe('item.custom_future')
    expect(labelItemTemplate('item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ')).toBe(
      'item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ',
    )
    expect(labelCardSource('upload')).toBe('上传')
  })

  it('AC-34 结局通关总结：本局回合/目标进度/生命值；无 goal 时省略目标段', () => {
    const goal = {
      title: '余烬哨站',
      description: '找到失踪的哨兵并点亮信标。',
      objectives: [
        { id: 'o1', text: '进入哨站', done: true },
        { id: 'o2', text: '找到老哨兵', done: false },
        { id: 'o3', text: '点亮信标', done: true },
      ],
    }
    expect(formatEndingSummary({ turn: 14, hp: 8, maxHp: 12, goal })).toBe(
      '本局 14 回合 · 目标 2/3 · 生命值 8/12',
    )
    expect(formatEndingSummary({ turn: 3, hp: 20, maxHp: 20, goal: null })).toBe(
      '本局 3 回合 · 生命值 20/20',
    )
  })
})
