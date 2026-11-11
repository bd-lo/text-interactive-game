import { describe, expect, it } from 'vitest'

import { ApiClientError } from '@/api/client'
import {
  checkScenarioFileName,
  checkScenarioFileSize,
  checkScenarioText,
  describeScenarioError,
  formatScenarioIssue,
  formatScenarioStats,
  labelEndingKind,
  scenarioDefinitionSections,
  SCENARIO_MAX_BYTES,
} from '@/utils/scenarios'

const STATS = { rooms: 4, items: 2, npcs: 1, objectives: 3, endings: 2, suggestions: 5 }

const DEFINITION = {
  schema_version: 1,
  slug: 'ember-watch',
  meta: { title: '余烬哨站（示例）', author: '玩家名' },
  player: { location: 'bridge' },
  rooms: [
    { key: 'bridge', name: '断桥', description: '风雪把桥面埋了一半。' },
    { key: 'hub', name: '哨站大厅', description: '火塘里剩着余烬。' },
  ],
  items: [{ key: 'flint', name: '火石', description: '一块打火石。' }],
  npcs: [
    {
      key: 'watchman',
      name: '守夜人',
      room: 'hub',
      dialogue: { default: '他抬了抬下巴。' },
    },
  ],
  goal: {
    title: '点亮信号灯',
    description: '让塔顶的火重新烧起来。',
    objectives: [
      { key: 'listen', text: '向守夜人打听情况' },
      { key: 'light', text: '带着火石登上塔顶' },
    ],
  },
  endings: [
    { key: 'victory', kind: 'win', title: '灯亮了', text: '塔顶的风很大。' },
    { key: 'fallen', kind: 'lose', title: '倒在雪里', text: '……' },
  ],
}

describe('场景导入客户端预检（256KB / UTF-8 / JSON 结构）', () => {
  it('文件大小：≤256KB 通过，超过给出中文原因', () => {
    expect(checkScenarioFileSize(1024)).toBe('')
    expect(checkScenarioFileSize(SCENARIO_MAX_BYTES)).toBe('')
    const error = checkScenarioFileSize(SCENARIO_MAX_BYTES + 1)
    expect(error).toContain('256KB')
    expect(error).toContain('文件过大')
  })

  it('文件名：只接受 .json（大小写不敏感）', () => {
    expect(checkScenarioFileName('tiny-tower.json')).toBe('')
    expect(checkScenarioFileName('TINY.JSON')).toBe('')
    expect(checkScenarioFileName('tiny-tower.txt')).toContain('只接受 .json')
    expect(checkScenarioFileName('')).toContain('没有名字')
  })

  it('文本预检：空/超限/非 UTF-8/语法错/根节点非对象 都被本地拦下', () => {
    expect(checkScenarioText('   ').error).toContain('内容为空')
    expect(checkScenarioText('{"a":"' + 'x'.repeat(SCENARIO_MAX_BYTES) + '"}').error).toContain(
      '256KB',
    )
    expect(checkScenarioText('{"name":"坏了\uFFFD"}').error).toContain('UTF-8')
    expect(checkScenarioText('{broken').error).toContain('JSON 语法错误')
    expect(checkScenarioText('[1,2,3]').error).toContain('根节点必须是对象')
  })

  it('文本预检：合法 JSON 对象通过并给出字节数', () => {
    const check = checkScenarioText(JSON.stringify(DEFINITION))
    expect(check.ok).toBe(true)
    expect(check.error).toBe('')
    expect(check.bytes).toBeGreaterThan(0)
  })
})

describe('场景错误文案（413/404/422 特例 + 统一映射）', () => {
  it('413 → 文件太大；404 → 找不到这个场景；网络错误 → 连接不上', () => {
    expect(describeScenarioError(new ApiClientError(413, 'INVALID_INPUT', 'too large')).title).toBe(
      '文件太大了',
    )
    const notFound = describeScenarioError(
      new ApiClientError(404, 'NOT_FOUND', 'scenario not found'),
    )
    expect(notFound.title).toBe('找不到这个场景')
    expect(notFound.message).toContain('可能不存在')
    expect(describeScenarioError(new TypeError('Failed to fetch')).title).toBe('连接不上服务器')
  })

  it('422 violations=[{path,message}] → 逐条可定位的中文错误（并兼容 loc/type/msg 旧形状）', () => {
    const error = describeScenarioError(
      new ApiClientError(422, 'INVALID_INPUT', '场景定义不合法', [
        { path: 'rooms[2].exits.east', message: '未知房间 key "hub2"' },
        { path: '', message: '缺少 schema_version' },
      ]),
    )
    expect(error.title).toBe('场景内容不符合格式')
    expect(error.message).toContain('rooms[2].exits.east：未知房间 key "hub2"')
    expect(error.message).toContain('缺少 schema_version')

    const legacy = describeScenarioError(
      new ApiClientError(422, 'INVALID_INPUT', 'bad', [
        { loc: ['body', 'scenario'], type: 'literal_error', msg: 'Input should be ...' },
      ]),
    )
    expect(legacy.message).toContain('场景')
    expect(legacy.message).toContain('取值不在允许范围')
  })
})

describe('场景展示辅助（统计/位置/结局类型/定义摘要）', () => {
  it('formatScenarioStats 输出 6 个计数；formatScenarioIssue 带位置与根级两种形态', () => {
    expect(formatScenarioStats(STATS)).toBe(
      '4 个房间 · 2 件物品 · 1 个角色 · 3 个目标 · 2 个结局 · 5 条建议',
    )
    expect(formatScenarioIssue({ path: 'rooms[2].exits.east', message: '未知房间 key' })).toBe(
      'rooms[2].exits.east：未知房间 key',
    )
    expect(formatScenarioIssue({ path: '', message: '仅提示' })).toBe('仅提示')
  })

  it('labelEndingKind：win/lose/neutral → 中文，未知回退原值', () => {
    expect(labelEndingKind('win')).toBe('胜利')
    expect(labelEndingKind('lose')).toBe('失败')
    expect(labelEndingKind('neutral')).toBe('中性')
    expect(labelEndingKind('mystery')).toBe('mystery')
  })

  it('scenarioDefinitionSections：房间/物品/NPC/目标/结局逐项派生（NPC 带房间名）', () => {
    const sections = scenarioDefinitionSections(DEFINITION)

    expect(sections.rooms.map((room) => room.name)).toEqual(['断桥', '哨站大厅'])
    expect(sections.items[0]?.name).toBe('火石')
    expect(sections.npcs[0]?.roomName).toBe('哨站大厅')
    expect(sections.goal?.title).toBe('点亮信号灯')
    expect(sections.goal?.objectives.map((objective) => objective.text)).toEqual([
      '向守夜人打听情况',
      '带着火石登上塔顶',
    ])
    expect(sections.endings.map((ending) => `${ending.kindLabel}:${ending.title}`)).toEqual([
      '胜利:灯亮了',
      '失败:倒在雪里',
    ])
  })

  it('scenarioDefinitionSections：缺字段/类型不符不炸，回退空值或 key', () => {
    expect(scenarioDefinitionSections({})).toEqual({
      rooms: [],
      items: [],
      npcs: [],
      goal: null,
      endings: [],
    })
    const weird = scenarioDefinitionSections({
      rooms: ['不是对象', { key: 'only_key' }, { key: 'k', name: 123 }],
      npcs: [{ key: 'n1', name: '无名', room: 'missing_room' }],
      goal: '不是对象',
      endings: [null, { key: 'e1', kind: 'win' }],
    })
    expect(weird.rooms).toEqual([
      { key: 'only_key', name: 'only_key', description: '' },
      { key: 'k', name: 'k', description: '' },
    ])
    expect(weird.npcs[0]?.roomName).toBe('missing_room')
    expect(weird.goal).toBeNull()
    expect(weird.endings[0]?.title).toBe('e1')
  })
})
