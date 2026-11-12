/**
 * N1-031 场景编辑器数据层：表单草稿 ↔ 场景定义 JSON 纯函数回归。
 *
 * 覆盖：默认最小场景、v1/v2 版本门禁、flags/条件、字段校验（slug/房间/HP/重复 key）、
 * 定义 → 草稿（v1 不擅自升级）、列表/出口/优先级映射、条件骨架与哈希幂等键。
 */
import { describe, expect, it } from 'vitest'

import {
  SCENARIO_CONDITION_KINDS,
  buildScenarioDefinition,
  conditionKind,
  conditionNodeCount,
  createCondition,
  defaultScenarioDraft,
  definitionSchemaVersion,
  emptyEndingDraft,
  emptyFlagDraft,
  emptyRoomDraft,
  joinListText,
  parseDefinitionToDraft,
  parseDraftFromJsonText,
  scenarioDraftHash,
  scenarioEditorSaveAction,
  splitListText,
  tryBuildScenarioDefinition,
  type ScenarioDraft,
} from '@/utils/scenarioEditor'

function draftWith(patch: (draft: ScenarioDraft) => void): ScenarioDraft {
  const draft = defaultScenarioDraft()
  patch(draft)
  return draft
}

describe('scenarioEditor 纯函数（表单 → JSON）', () => {
  it('默认草稿生成最小 v2 场景：schema_version/meta/player/1 房间，可再解析', () => {
    const definition = buildScenarioDefinition(defaultScenarioDraft())

    expect(definition.schema_version).toBe(2)
    expect(definition.slug).toBe('my-first-scene')
    expect(definition.meta).toEqual({ title: '我的第一幕' })
    expect(definition.player).toEqual({ name: '旅人', hp: 20, max_hp: 20, location: 'entry' })
    expect(Array.isArray(definition.rooms)).toBe(true)
    expect((definition.rooms as unknown[]).length).toBe(1)
    expect(definition.goal).toBeUndefined()
    expect(definition.endings).toBeUndefined()

    // 往返：build → parse → build 仍等价
    const roundtrip = buildScenarioDefinition(parseDefinitionToDraft(definition))
    expect(roundtrip).toEqual(definition)
  })

  it('v2 flags + 9 类 v2 谓词/组合子都按「恰好一个键」的 JSON 对象产出', () => {
    const condition = {
      all: [
        { flag: 'arrived' },
        { objective_done: 'reach' },
        { has_item_tag_count: { tag: 'quest', at_least: 2 } },
        { item_in_room: { item: 'flint', room: 'hall' } },
        { npc_in_room: { npc: 'keeper', room: 'hall' } },
        { turn_gte: 2 },
        { turn_lte: 10 },
        { world_time_gte: 6 },
        { world_time_lte: 22 },
      ],
    }
    const draft = draftWith((current) => {
      current.schemaVersion = 2
      current.flags = [{ key: 'arrived', when: { in_room: 'hall' } }]
      current.goal = {
        enabled: true,
        title: '点亮灯',
        description: '',
        objectives: [{ key: 'reach', text: '进入大厅', when: condition }],
      }
    })

    const definition = buildScenarioDefinition(draft)
    expect(definition.flags).toEqual([{ key: 'arrived', when: { in_room: 'hall' } }])
    const goal = definition.goal as { objectives: Array<{ when: unknown }> }
    expect(goal.objectives[0]?.when).toEqual(condition)
    // 每个 v2 原子仍是唯一键
    const children = (goal.objectives[0]?.when as { all: Array<Record<string, unknown>> }).all
    for (const child of children) {
      expect(Object.keys(child)).toHaveLength(1)
    }
  })

  it('schema_version=1 出现 flags → 本地 build 明确拒绝（不静默升级）', () => {
    const draft = draftWith((current) => {
      current.schemaVersion = 1
      current.flags = [emptyFlagDraft()]
    })
    draft.flags[0] = { key: 'flag_a', when: { in_room: 'entry' } }

    expect(tryBuildScenarioDefinition(draft)).toEqual({
      ok: false,
      error: expect.stringContaining('schema_version=1 不能包含 flags'),
    })
  })

  it('slug/标题/房间描述/HP 上限/重复 key 的本地校验给出中文错误', () => {
    expect(tryBuildScenarioDefinition(draftWith((draft) => (draft.slug = 'Bad Slug!')))).toMatchObject({
      ok: false,
      error: expect.stringContaining('slug'),
    })
    expect(tryBuildScenarioDefinition(draftWith((draft) => (draft.meta.title = '  ')))).toMatchObject({
      ok: false,
      error: expect.stringContaining('场景标题'),
    })
    expect(
      tryBuildScenarioDefinition(draftWith((draft) => (draft.rooms[0]!.description = ''))),
    ).toMatchObject({ ok: false, error: expect.stringContaining('描述') })
    expect(
      tryBuildScenarioDefinition(draftWith((draft) => (draft.player.hp = '30'))),
    ).toMatchObject({ ok: false, error: expect.stringContaining('不能大于上限') })
    expect(
      tryBuildScenarioDefinition(
        draftWith((draft) => {
          draft.rooms.push(emptyRoomDraft(2))
          draft.rooms[1]!.key = 'entry'
        }),
      ),
    ).toMatchObject({ ok: false, error: expect.stringContaining('重复') })
    expect(
      tryBuildScenarioDefinition(draftWith((draft) => (draft.player.hp = 'abc'))),
    ).toMatchObject({ ok: false, error: expect.stringContaining('整数') })
  })

  it('房间出口只保留非空方向；列表文本去空；结局 priority 缺省 0', () => {
    const draft = draftWith((current) => {
      current.rooms[0]!.exits.east = 'hall'
      current.rooms[0]!.exits.north = '   '
      current.rooms[0]!.itemsText = 'flint, , potion'
      current.endings = [emptyEndingDraft(1)]
      current.endings[0]!.priority = ''
    })
    const definition = buildScenarioDefinition(draft)
    const room = (definition.rooms as Array<Record<string, unknown>>)[0]!
    expect(room.exits).toEqual({ east: 'hall' })
    expect(room.items).toEqual(['flint', 'potion'])
    expect((definition.endings as Array<Record<string, unknown>>)[0]?.priority).toBe(0)
  })
})

describe('scenarioEditor 纯函数（JSON → 表单 / 兼容）', () => {
  const V1 = {
    schema_version: 1,
    slug: 'tiny-tower',
    meta: { title: '小塔', author: '示例' },
    player: { location: 'gate', hp: 10, max_hp: 10 },
    rooms: [
      { key: 'gate', name: '塔门', description: '虚掩的木门。', exits: { north: 'hall' } },
      { key: 'hall', name: '塔厅', description: '墙上有灯。', items: ['flint'] },
    ],
    items: [{ key: 'flint', name: '火石', tags: ['quest'] }],
    npcs: [
      {
        key: 'keeper',
        name: '守夜人',
        room: 'hall',
        dialogue: { default: '嗯。', topics: [{ keywords: ['灯'], reply: '去地牢。', dialogue_state: 'briefed' }] },
      },
    ],
    goal: { title: '点亮', objectives: [{ key: 'climb', text: '进入塔厅', when: { in_room: 'hall' } }] },
    endings: [{ key: 'win', kind: 'win', title: '亮了', text: '灯亮了。', priority: 10, when: { in_room: 'hall' } }],
    suggestions: [{ text: '往上走', when: { in_room: 'hall' } }],
  }

  it('v1 定义 → 草稿：版本保持 1（不擅自升级），字段完整映射', () => {
    const draft = parseDefinitionToDraft(V1)

    expect(draft.schemaVersion).toBe(1)
    expect(definitionSchemaVersion(V1)).toBe(1)
    expect(draft.rooms).toHaveLength(2)
    expect(draft.rooms[0]?.exits.north).toBe('hall')
    expect(draft.items[0]?.tagsText).toBe('quest')
    expect(draft.npcs[0]?.topics[0]?.dialogueState).toBe('briefed')
    expect(draft.goal.enabled).toBe(true)
    expect(draft.goal.objectives[0]?.when).toEqual({ in_room: 'hall' })
    expect(draft.endings[0]?.priority).toBe('10')
    expect(draft.suggestions[0]?.when).toEqual({ in_room: 'hall' })
  })

  it('v2 定义可解析并回写；非法 schema_version/JSON 给出可读错误', () => {
    const v2 = {
      ...V1,
      schema_version: 2,
      flags: [{ key: 'briefed', when: { npc_state: { npc: 'keeper', state: 'briefed' } } }],
    }
    const draft = parseDefinitionToDraft(v2)
    expect(draft.schemaVersion).toBe(2)
    expect(draft.flags[0]?.when).toEqual({ npc_state: { npc: 'keeper', state: 'briefed' } })

    expect(() => parseDefinitionToDraft({ ...V1, schema_version: 3 })).toThrow(/schema_version/)
    expect(() => parseDraftFromJsonText('{broken')).toThrow(/JSON 语法错误/)
    expect(() => parseDraftFromJsonText('[]')).toThrow(/根节点必须是对象/)
  })

  it('列表文本/条件工具：split/join、条件类型与节点计数', () => {
    expect(splitListText('quest, weapon、potion； flint  ')).toEqual([
      'quest',
      'weapon',
      'potion',
      'flint',
    ])
    expect(joinListText(['a', 'b'])).toBe('a, b')
    expect(joinListText([1, 'b'])).toBe('b')

    for (const option of SCENARIO_CONDITION_KINDS) {
      const condition = createCondition(option.kind)
      expect(Object.keys(condition)).toHaveLength(1)
      expect(conditionKind(condition)).toBe(option.kind)
      const isCombo = option.kind === 'all' || option.kind === 'any' || option.kind === 'not'
      expect(conditionNodeCount(condition)).toBe(isCombo ? 2 : 1)
    }
    expect(
      conditionNodeCount({
        all: [
          { in_room: 'a' },
          { any: [{ has_item: 'x' }, { not: { flag: 'f' } }] },
        ],
      }),
    ).toBe(6)
    expect(conditionKind({ in_room: 'a', flag: 'b' })).toBe('')
    expect(conditionKind(null)).toBe('')
  })

  it('保存幂等键：同内容同哈希、内容变化换键（手动重试复用/编辑后换新键）', () => {
    const first = JSON.stringify(V1)
    const second = JSON.stringify({ ...V1, slug: 'other-scene' })
    expect(scenarioDraftHash(first)).toBe(scenarioDraftHash(first))
    expect(scenarioDraftHash(first)).not.toBe(scenarioDraftHash(second))
    expect(scenarioEditorSaveAction(first)).toBe(`scenario-editor-save:${scenarioDraftHash(first)}`)
  })
})
