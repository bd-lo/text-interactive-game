/**
 * 场景编辑器数据层（N1-031）：表单草稿 ↔ 场景定义 JSON 的纯函数映射。
 *
 * 口径（`docs/design/scenario-dsl-v2.md` §4/§5、`user-scenario-modules.md` §2/§6）：
 * - 编辑器**只产出 JSON**：`buildScenarioDefinition(draft)` 的结果只用于「预览/下载」；
 *   保存时仍把 JSON 原文提交 `POST /api/v1/scenarios`，服务端校验/白名单/幂等是唯一权威；
 * - v1 逐字兼容：`schema_version=1` 不允许 `flags`/v2 谓词（版本门禁在服务端）；载入 v1 场景时
 *   **不擅自升级**，由作者显式把版本改成 2；
 * - 表单里的数字/列表用文本承载（`"hp": "12"`、`tagsText: "quest, weapon"`），build 时转成 JSON 类型；
 * - 无脚本执行：条件节点只是「恰好一个键」的 JSON 对象，未知/非法由服务端给出带 JSON 路径的错误。
 */

import type { ScenarioDefinition } from '@/api/types'

/** 场景定义里的一个条件节点（恰好一个键的 JSON 对象；由服务端白名单校验）。 */
export type ScenarioCondition = Record<string, unknown>
/** 场景包版本（v1 冻结 / v2 加法；其他值由服务端拒绝）。 */
export type ScenarioSchemaVersion = 1 | 2

/** v2 白名单谓词/组合子（与 `design/scenario-dsl-v2.md` §3、后端 `_CONDITION_FIELDS` 一致）。 */
export type ScenarioConditionKind =
  | 'in_room'
  | 'has_item'
  | 'has_item_tag'
  | 'npc_state'
  | 'hp_lte'
  | 'hp_gte'
  | 'flag'
  | 'objective_done'
  | 'has_item_tag_count'
  | 'item_in_room'
  | 'npc_in_room'
  | 'turn_gte'
  | 'turn_lte'
  | 'world_time_gte'
  | 'world_time_lte'
  | 'all'
  | 'any'
  | 'not'
  | 'always'

export interface ScenarioConditionKindOption {
  kind: ScenarioConditionKind
  label: string
  group: 'v1 原子' | 'v2 原子' | '组合子'
  hint: string
}

/** 条件种类清单（编辑器下拉/占位文案；顺序即展示顺序）。 */
export const SCENARIO_CONDITION_KINDS: readonly ScenarioConditionKindOption[] = [
  { kind: 'in_room', label: '在房间（in_room）', group: 'v1 原子', hint: '玩家当前所在房间' },
  { kind: 'has_item', label: '持有物品（has_item）', group: 'v1 原子', hint: '行囊里有该物品 key' },
  { kind: 'has_item_tag', label: '持有标签物品（has_item_tag）', group: 'v1 原子', hint: '任一物品带该标签' },
  { kind: 'npc_state', label: 'NPC 对话态（npc_state）', group: 'v1 原子', hint: 'NPC 的 dialogue_state 等于该值' },
  { kind: 'hp_lte', label: '生命值 ≤（hp_lte）', group: 'v1 原子', hint: '玩家 HP 小于等于 N' },
  { kind: 'hp_gte', label: '生命值 ≥（hp_gte）', group: 'v1 原子', hint: '玩家 HP 大于等于 N' },
  { kind: 'flag', label: '剧情开关（flag）', group: 'v2 原子', hint: '引用一个已声明的 flag' },
  { kind: 'objective_done', label: '目标完成（objective_done）', group: 'v2 原子', hint: '引用一个已声明的目标 key' },
  {
    kind: 'has_item_tag_count',
    label: '持有标签数量（has_item_tag_count）',
    group: 'v2 原子',
    hint: '行囊中该标签实例数 ≥ N（1..64）',
  },
  { kind: 'item_in_room', label: '物品仍在房间（item_in_room）', group: 'v2 原子', hint: '该物品尚未被拿走' },
  { kind: 'npc_in_room', label: 'NPC 在房间（npc_in_room）', group: 'v2 原子', hint: 'NPC 当前所在房间' },
  { kind: 'turn_gte', label: '回合 ≥（turn_gte）', group: 'v2 原子', hint: '第 N 回合及之后' },
  { kind: 'turn_lte', label: '回合 ≤（turn_lte）', group: 'v2 原子', hint: '第 N 回合及之前' },
  { kind: 'world_time_gte', label: '世界时间 ≥（world_time_gte）', group: 'v2 原子', hint: '世界时间阈值' },
  { kind: 'world_time_lte', label: '世界时间 ≤（world_time_lte）', group: 'v2 原子', hint: '世界时间阈值' },
  { kind: 'all', label: '全部满足（all）', group: '组合子', hint: '1..16 个子条件全部满足' },
  { kind: 'any', label: '任一满足（any）', group: '组合子', hint: '1..16 个子条件任一满足' },
  { kind: 'not', label: '取反（not）', group: '组合子', hint: '子条件不满足' },
  { kind: 'always', label: '恒真/恒假（always）', group: '组合子', hint: 'true 恒真 / false 恒假' },
]

/** 编辑器本地草稿错误（字段不完整/类型不对；用户可读中文）。 */
export class ScenarioDraftError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ScenarioDraftError'
  }
}

export interface ScenarioMetaDraft {
  title: string
  author: string
  description: string
  /** 逗号/空格分隔的标签文本（转换时去空去重）。 */
  tagsText: string
}

export interface ScenarioPlayerDraft {
  name: string
  hp: string
  maxHp: string
  location: string
  inventoryText: string
}

export interface ScenarioExitsDraft {
  north: string
  south: string
  east: string
  west: string
  up: string
  down: string
  in: string
  out: string
}

export interface ScenarioRoomDraft {
  key: string
  name: string
  description: string
  exits: ScenarioExitsDraft
  itemsText: string
  npcsText: string
  ambientTagsText: string
  hpDelta: string
  hint: string
}

export interface ScenarioItemDraft {
  key: string
  name: string
  description: string
  tagsText: string
  heal: string
  damage: string
  rarity: string
}

export interface ScenarioTopicDraft {
  keywordsText: string
  reply: string
  dialogueState: string
}

export interface ScenarioNpcDraft {
  key: string
  name: string
  room: string
  description: string
  dialogueDefault: string
  briefedReply: string
  topics: ScenarioTopicDraft[]
}

export interface ScenarioFlagDraft {
  key: string
  when: ScenarioCondition | null
}

export interface ScenarioObjectiveDraft {
  key: string
  text: string
  when: ScenarioCondition | null
}

export interface ScenarioGoalDraft {
  enabled: boolean
  title: string
  description: string
  objectives: ScenarioObjectiveDraft[]
}

export interface ScenarioEndingDraft {
  key: string
  kind: 'win' | 'lose'
  title: string
  text: string
  priority: string
  when: ScenarioCondition | null
}

export interface ScenarioSuggestionDraft {
  text: string
  when: ScenarioCondition | null
}

export interface ScenarioDraft {
  schemaVersion: ScenarioSchemaVersion
  slug: string
  meta: ScenarioMetaDraft
  player: ScenarioPlayerDraft
  rooms: ScenarioRoomDraft[]
  items: ScenarioItemDraft[]
  npcs: ScenarioNpcDraft[]
  flags: ScenarioFlagDraft[]
  goal: ScenarioGoalDraft
  endings: ScenarioEndingDraft[]
  suggestions: ScenarioSuggestionDraft[]
}

/** 场景包版本可选值（编辑器下拉；v1 冻结 / v2 加法）。 */
export const SCENARIO_SCHEMA_VERSION_OPTIONS = [1, 2] as const

/** 出口方向（与后端 `Direction` 一致）。 */
export const SCENARIO_EXIT_DIRECTIONS = [
  'north',
  'south',
  'east',
  'west',
  'up',
  'down',
  'in',
  'out',
] as const
export type ScenarioExitDirection = (typeof SCENARIO_EXIT_DIRECTIONS)[number]

/** 物品稀有度（可选；与既有 `Item` 语义一致）。 */
export const SCENARIO_RARITIES = ['common', 'uncommon', 'rare', 'epic', 'legendary'] as const

/** 出口方向中文（表单标签用；未知回退方向本身）。 */
export const SCENARIO_EXIT_LABELS: Readonly<Record<ScenarioExitDirection, string>> = {
  north: '北',
  south: '南',
  east: '东',
  west: '西',
  up: '上',
  down: '下',
  in: '进',
  out: '出',
}

function emptyExitsDraft(): ScenarioExitsDraft {
  return { north: '', south: '', east: '', west: '', up: '', down: '', in: '', out: '' }
}

export function emptyRoomDraft(index = 1): ScenarioRoomDraft {
  return {
    key: index === 1 ? 'entry' : `room_${index}`,
    name: index === 1 ? '起点' : `房间 ${index}`,
    description: index === 1 ? '你站在故事的起点。' : '这里还没有描述。',
    exits: emptyExitsDraft(),
    itemsText: '',
    npcsText: '',
    ambientTagsText: '',
    hpDelta: '',
    hint: '',
  }
}

export function emptyItemDraft(index = 1): ScenarioItemDraft {
  return {
    key: `item_${index}`,
    name: `物品 ${index}`,
    description: '',
    tagsText: '',
    heal: '',
    damage: '',
    rarity: '',
  }
}

export function emptyTopicDraft(): ScenarioTopicDraft {
  return { keywordsText: '', reply: '', dialogueState: '' }
}

export function emptyNpcDraft(index = 1): ScenarioNpcDraft {
  return {
    key: `npc_${index}`,
    name: `角色 ${index}`,
    room: '',
    description: '',
    dialogueDefault: '',
    briefedReply: '',
    topics: [],
  }
}

export function emptyFlagDraft(): ScenarioFlagDraft {
  return { key: '', when: null }
}

export function emptyObjectiveDraft(index = 1): ScenarioObjectiveDraft {
  return { key: `objective_${index}`, text: `目标 ${index}`, when: null }
}

export function emptyEndingDraft(index = 1): ScenarioEndingDraft {
  return {
    key: `ending_${index}`,
    kind: 'win',
    title: `结局 ${index}`,
    text: '故事在这里落下了帷幕。',
    priority: '0',
    when: null,
  }
}

export function emptySuggestionDraft(): ScenarioSuggestionDraft {
  return { text: '', when: null }
}

/** 编辑器默认草稿：v2、最小可玩（1 房间/玩家/无目标结局，服务端可校验通过）。 */
export function defaultScenarioDraft(): ScenarioDraft {
  return {
    schemaVersion: 2,
    slug: 'my-first-scene',
    meta: { title: '我的第一幕', author: '', description: '', tagsText: '' },
    player: { name: '旅人', hp: '20', maxHp: '20', location: 'entry', inventoryText: '' },
    rooms: [emptyRoomDraft(1)],
    items: [],
    npcs: [],
    flags: [],
    goal: { enabled: false, title: '完成目标', description: '', objectives: [] },
    endings: [],
    suggestions: [],
  }
}

/** 列表文本 → 字符串数组（逗号/顿号/分号/空白分隔，去空）。 */
export function splitListText(text: string): string[] {
  return text
    .split(/[,，、;；\s]+/)
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

/** 字符串数组 → 列表文本（逗号分隔；展示用）。 */
export function joinListText(items: readonly unknown[]): string {
  return items.filter((item): item is string => typeof item === 'string').join(', ')
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asCondition(value: unknown): ScenarioCondition | null {
  const record = asRecord(value)
  return record !== null && Object.keys(record).length === 1 ? record : null
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNumberText(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
}

function requiredText(value: string, label: string): string {
  const trimmed = value.trim()
  if (trimmed === '') {
    throw new ScenarioDraftError(`请填写${label}`)
  }
  return trimmed
}

function optionalText(value: string): string | undefined {
  const trimmed = value.trim()
  return trimmed === '' ? undefined : trimmed
}

function parseOptionalInt(value: string, label: string, min = Number.MIN_SAFE_INTEGER): number | undefined {
  const trimmed = value.trim()
  if (trimmed === '') {
    return undefined
  }
  const parsed = Number(trimmed)
  if (!Number.isInteger(parsed)) {
    throw new ScenarioDraftError(`${label} 必须是整数（当前「${trimmed}」）`)
  }
  if (parsed < min) {
    throw new ScenarioDraftError(`${label} 不能小于 ${min}（当前 ${parsed}）`)
  }
  return parsed
}

function uniqueKeys(
  entries: ReadonlyArray<{ key: string }>,
  label: string,
  allowEmpty = false,
): void {
  const seen = new Set<string>()
  for (const entry of entries) {
    if (entry.key === '') {
      if (allowEmpty) {
        continue
      }
      throw new ScenarioDraftError(`${label}的 key 不能为空`)
    }
    if (seen.has(entry.key)) {
      throw new ScenarioDraftError(`${label}的 key「${entry.key}」重复了；同一类型内 key 必须唯一`)
    }
    seen.add(entry.key)
  }
}

function buildRoom(room: ScenarioRoomDraft, index: number): Record<string, unknown> {
  const label = `第 ${index + 1} 个房间`
  const built: Record<string, unknown> = {
    key: requiredText(room.key, `${label}的 key`),
    name: requiredText(room.name, `${label}的名称`),
    description: requiredText(room.description, `${label}的描述`),
  }
  const exits: Record<string, string> = {}
  for (const direction of SCENARIO_EXIT_DIRECTIONS) {
    const target = room.exits[direction].trim()
    if (target !== '') {
      exits[direction] = target
    }
  }
  if (Object.keys(exits).length > 0) {
    built.exits = exits
  }
  const itemKeys = splitListText(room.itemsText)
  if (itemKeys.length > 0) {
    built.items = itemKeys
  }
  const npcKeys = splitListText(room.npcsText)
  if (npcKeys.length > 0) {
    built.npcs = npcKeys
  }
  const ambientTags = splitListText(room.ambientTagsText)
  if (ambientTags.length > 0) {
    built.ambient_tags = ambientTags
  }
  const hpDelta = parseOptionalInt(room.hpDelta, `${label}的进入扣血`)
  const hint = optionalText(room.hint)
  if (hpDelta !== undefined || hint !== undefined) {
    built.on_enter = {
      ...(hpDelta === undefined ? {} : { hp_delta: hpDelta }),
      ...(hint === undefined ? {} : { hint }),
    }
  }
  return built
}

function buildItem(item: ScenarioItemDraft, index: number): Record<string, unknown> {
  const label = `第 ${index + 1} 个物品`
  const built: Record<string, unknown> = {
    key: requiredText(item.key, `${label}的 key`),
    name: requiredText(item.name, `${label}的名称`),
  }
  const description = optionalText(item.description)
  if (description !== undefined) {
    built.description = description
  }
  const tags = splitListText(item.tagsText)
  if (tags.length > 0) {
    built.tags = tags
  }
  const heal = parseOptionalInt(item.heal, `${label}的治疗量`)
  const damage = parseOptionalInt(item.damage, `${label}的伤害`)
  if (heal !== undefined || damage !== undefined) {
    built.stats = {
      ...(heal === undefined ? {} : { heal }),
      ...(damage === undefined ? {} : { damage }),
    }
  }
  const rarity = item.rarity.trim()
  if (rarity !== '') {
    if (!(SCENARIO_RARITIES as readonly string[]).includes(rarity)) {
      throw new ScenarioDraftError(`${label}的稀有度「${rarity}」不合法（可选：${SCENARIO_RARITIES.join('/')}）`)
    }
    built.rarity = rarity
  }
  return built
}

function buildTopics(npc: ScenarioNpcDraft, index: number): Record<string, unknown>[] {
  return npc.topics.map((topic, topicIndex) => {
    const label = `第 ${index + 1} 个角色的第 ${topicIndex + 1} 条对话`
    const keywords = splitListText(topic.keywordsText)
    if (keywords.length === 0) {
      throw new ScenarioDraftError(`${label}缺少关键词（至少 1 个，用逗号分隔）`)
    }
    const built: Record<string, unknown> = {
      keywords,
      reply: requiredText(topic.reply, `${label}的回复`),
    }
    const dialogueState = optionalText(topic.dialogueState)
    if (dialogueState !== undefined) {
      built.dialogue_state = dialogueState
    }
    return built
  })
}

function buildNpc(npc: ScenarioNpcDraft, index: number): Record<string, unknown> {
  const label = `第 ${index + 1} 个角色`
  const built: Record<string, unknown> = {
    key: requiredText(npc.key, `${label}的 key`),
    name: requiredText(npc.name, `${label}的名称`),
    room: requiredText(npc.room, `${label}所在房间`),
  }
  const description = optionalText(npc.description)
  if (description !== undefined) {
    built.description = description
  }
  const defaultReply = optionalText(npc.dialogueDefault)
  const briefedReply = optionalText(npc.briefedReply)
  const topics = buildTopics(npc, index)
  if (defaultReply !== undefined || briefedReply !== undefined || topics.length > 0) {
    built.dialogue = {
      ...(defaultReply === undefined ? {} : { default: defaultReply }),
      ...(briefedReply === undefined ? {} : { briefed_reply: briefedReply }),
      ...(topics.length === 0 ? {} : { topics }),
    }
  }
  return built
}

function buildCondition(condition: ScenarioCondition, label: string): ScenarioCondition {
  const keys = Object.keys(condition)
  if (keys.length !== 1) {
    throw new ScenarioDraftError(`${label}必须是「恰好一个键」的条件对象（当前 ${keys.length} 个键）`)
  }
  return condition
}

function buildFlag(flag: ScenarioFlagDraft, index: number): Record<string, unknown> {
  const label = `第 ${index + 1} 个剧情开关`
  const key = requiredText(flag.key, `${label}的 key`)
  if (flag.when === null) {
    throw new ScenarioDraftError(`剧情开关「${key}」需要设置触发条件`)
  }
  return { key, when: buildCondition(flag.when, `剧情开关「${key}」的条件`) }
}

function buildObjective(objective: ScenarioObjectiveDraft, index: number): Record<string, unknown> {
  const label = `第 ${index + 1} 个目标`
  const built: Record<string, unknown> = {
    key: requiredText(objective.key, `${label}的 key`),
    text: requiredText(objective.text, `${label}的文案`),
  }
  if (objective.when !== null) {
    built.when = buildCondition(objective.when, `${label}的条件`)
  }
  return built
}

function buildEnding(ending: ScenarioEndingDraft, index: number): Record<string, unknown> {
  const label = `第 ${index + 1} 个结局`
  const built: Record<string, unknown> = {
    key: requiredText(ending.key, `${label}的 key`),
    kind: ending.kind,
    title: requiredText(ending.title, `${label}的标题`),
    text: requiredText(ending.text, `${label}的正文`),
    priority: parseOptionalInt(ending.priority, `${label}的优先级`) ?? 0,
  }
  if (ending.when !== null) {
    built.when = buildCondition(ending.when, `${label}的条件`)
  }
  return built
}

/**
 * 草稿 → 场景定义 JSON（纯函数）；字段不完整/类型不对抛 `ScenarioDraftError`。
 *
 * 注意：结果只是**候选 JSON**；是否合法以服务端 `POST /scenarios/validate` 为准（编辑器不旁路）。
 */
export function buildScenarioDefinition(draft: ScenarioDraft): ScenarioDefinition {
  const schemaVersion: ScenarioSchemaVersion = draft.schemaVersion === 2 ? 2 : 1
  const slug = requiredText(draft.slug, 'slug（场景标识）')
  if (!/^[a-z0-9-]{2,32}$/.test(slug)) {
    throw new ScenarioDraftError('slug 只能用小写字母/数字/连字符，长度 2–32（例如 my-first-scene）')
  }

  const rooms = draft.rooms.map((room, index) => buildRoom(room, index))
  if (rooms.length === 0) {
    throw new ScenarioDraftError('场景至少需要 1 个房间')
  }
  uniqueKeys(
    rooms.map((room) => ({ key: String(room.key) })),
    '房间',
  )

  const items = draft.items
    .filter((item) => item.key.trim() !== '' || item.name.trim() !== '')
    .map((item, index) => buildItem(item, index))
  uniqueKeys(
    items.map((item) => ({ key: String(item.key) })),
    '物品',
  )

  const npcs = draft.npcs
    .filter((npc) => npc.key.trim() !== '' || npc.name.trim() !== '')
    .map((npc, index) => buildNpc(npc, index))
  uniqueKeys(
    npcs.map((npc) => ({ key: String(npc.key) })),
    '角色',
  )

  const flags = draft.flags
    .filter((flag) => flag.key.trim() !== '')
    .map((flag, index) => buildFlag(flag, index))
  if (schemaVersion === 1 && flags.length > 0) {
    throw new ScenarioDraftError(
      'schema_version=1 不能包含 flags（v2 字段）：请先把「场景包版本」改为 2，或删除剧情开关',
    )
  }
  uniqueKeys(
    flags.map((flag) => ({ key: String(flag.key) })),
    '剧情开关',
  )

  const meta: Record<string, unknown> = {
    title: requiredText(draft.meta.title, '场景标题'),
  }
  const author = optionalText(draft.meta.author)
  if (author !== undefined) {
    meta.author = author
  }
  const description = optionalText(draft.meta.description)
  if (description !== undefined) {
    meta.description = description
  }
  const tags = splitListText(draft.meta.tagsText)
  if (tags.length > 0) {
    meta.tags = tags
  }

  const playerName = optionalText(draft.player.name) ?? '旅人'
  const hp = parseOptionalInt(draft.player.hp, '玩家 HP', 0) ?? 20
  const maxHp = parseOptionalInt(draft.player.maxHp, '玩家 HP 上限', 1) ?? 20
  if (hp > maxHp) {
    throw new ScenarioDraftError(`玩家 HP（${hp}）不能大于上限（${maxHp}）`)
  }
  const inventory = splitListText(draft.player.inventoryText)
  const player: Record<string, unknown> = {
    name: playerName,
    hp,
    max_hp: maxHp,
    location: requiredText(draft.player.location, '玩家初始房间'),
  }
  if (inventory.length > 0) {
    player.inventory = inventory
  }

  const definition: ScenarioDefinition = {
    schema_version: schemaVersion,
    slug,
    meta,
    player,
    rooms,
  }
  if (items.length > 0) {
    definition.items = items
  }
  if (npcs.length > 0) {
    definition.npcs = npcs
  }
  if (flags.length > 0) {
    definition.flags = flags
  }

  if (draft.goal.enabled) {
    const objectives = draft.goal.objectives.map((objective, index) =>
      buildObjective(objective, index),
    )
    if (objectives.length === 0) {
      throw new ScenarioDraftError('启用目标后至少需要 1 条子目标')
    }
    const goal: Record<string, unknown> = {
      title: requiredText(draft.goal.title, '目标标题'),
      objectives,
    }
    const goalDescription = optionalText(draft.goal.description)
    if (goalDescription !== undefined) {
      goal.description = goalDescription
    }
    definition.goal = goal
  }

  const endings = draft.endings
    .filter((ending) => ending.key.trim() !== '' || ending.title.trim() !== '')
    .map((ending, index) => buildEnding(ending, index))
  uniqueKeys(
    endings.map((ending) => ({ key: String(ending.key) })),
    '结局',
  )
  if (endings.length > 0) {
    definition.endings = endings
  }

  const suggestions = draft.suggestions
    .filter((suggestion) => suggestion.text.trim() !== '')
    .map((suggestion) => {
      const built: Record<string, unknown> = { text: suggestion.text.trim() }
      if (suggestion.when !== null) {
        built.when = buildCondition(suggestion.when, '建议动作的条件')
      }
      return built
    })
  if (suggestions.length > 0) {
    definition.suggestions = suggestions
  }

  return definition
}

/** build 的 Result 版本（表单/JSON 预览用；失败不抛让 UI 展示中文错误）。 */
export function tryBuildScenarioDefinition(
  draft: ScenarioDraft,
): { ok: true; definition: ScenarioDefinition; text: string } | { ok: false; error: string } {
  try {
    const definition = buildScenarioDefinition(draft)
    return { ok: true, definition, text: JSON.stringify(definition, null, 2) }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

/** 场景定义 `schema_version`（无法识别返回 0；不擅自升级）。 */
export function definitionSchemaVersion(definition: ScenarioDefinition): 1 | 2 | 0 {
  const value = definition.schema_version
  return value === 1 || value === 2 ? value : 0
}

function parseExits(value: unknown): ScenarioExitsDraft {
  const exits = emptyExitsDraft()
  const record = asRecord(value)
  if (record === null) {
    return exits
  }
  for (const direction of SCENARIO_EXIT_DIRECTIONS) {
    const target = record[direction]
    if (typeof target === 'string') {
      exits[direction] = target
    }
  }
  return exits
}

function parseRoom(value: unknown, index: number): ScenarioRoomDraft {
  const room = asRecord(value) ?? {}
  const onEnter = asRecord(room.on_enter) ?? {}
  return {
    key: asString(room.key, `room_${index + 1}`),
    name: asString(room.name),
    description: asString(room.description),
    exits: parseExits(room.exits),
    itemsText: joinListText(Array.isArray(room.items) ? room.items : []),
    npcsText: joinListText(Array.isArray(room.npcs) ? room.npcs : []),
    ambientTagsText: joinListText(Array.isArray(room.ambient_tags) ? room.ambient_tags : []),
    hpDelta: asNumberText(onEnter.hp_delta),
    hint: asString(onEnter.hint),
  }
}

function parseItem(value: unknown, index: number): ScenarioItemDraft {
  const item = asRecord(value) ?? {}
  const stats = asRecord(item.stats) ?? {}
  return {
    key: asString(item.key, `item_${index + 1}`),
    name: asString(item.name),
    description: asString(item.description),
    tagsText: joinListText(Array.isArray(item.tags) ? item.tags : []),
    heal: asNumberText(stats.heal),
    damage: asNumberText(stats.damage),
    rarity: asString(item.rarity),
  }
}

function parseTopic(value: unknown): ScenarioTopicDraft {
  const topic = asRecord(value) ?? {}
  return {
    keywordsText: joinListText(Array.isArray(topic.keywords) ? topic.keywords : []),
    reply: asString(topic.reply),
    dialogueState: asString(topic.dialogue_state),
  }
}

function parseNpc(value: unknown, index: number): ScenarioNpcDraft {
  const npc = asRecord(value) ?? {}
  const dialogue = asRecord(npc.dialogue) ?? {}
  return {
    key: asString(npc.key, `npc_${index + 1}`),
    name: asString(npc.name),
    room: asString(npc.room),
    description: asString(npc.description),
    dialogueDefault: asString(dialogue.default),
    briefedReply: asString(dialogue.briefed_reply),
    topics: Array.isArray(dialogue.topics) ? dialogue.topics.map((topic) => parseTopic(topic)) : [],
  }
}

function parseFlag(value: unknown): ScenarioFlagDraft {
  const flag = asRecord(value) ?? {}
  return { key: asString(flag.key), when: asCondition(flag.when) }
}

function parseObjective(value: unknown, index: number): ScenarioObjectiveDraft {
  const objective = asRecord(value) ?? {}
  return {
    key: asString(objective.key, `objective_${index + 1}`),
    text: asString(objective.text),
    when: asCondition(objective.when),
  }
}

function parseEnding(value: unknown, index: number): ScenarioEndingDraft {
  const ending = asRecord(value) ?? {}
  const key = asString(ending.key, `ending_${index + 1}`)
  return {
    key,
    kind: ending.kind === 'lose' ? 'lose' : 'win',
    title: asString(ending.title, key),
    text: asString(ending.text),
    priority: asNumberText(ending.priority) === '' ? '0' : asNumberText(ending.priority),
    when: asCondition(ending.when),
  }
}

function parseSuggestion(value: unknown): ScenarioSuggestionDraft {
  const suggestion = asRecord(value) ?? {}
  return { text: asString(suggestion.text), when: asCondition(suggestion.when) }
}

/**
 * 场景定义 → 编辑器草稿（载入详情/粘贴 JSON 用）。
 *
 * 容错策略：缺省字段回退编辑器默认值；未知字段忽略（保存时以表单 build 的结果为准）。
 * 仅根节点/schema_version 非法时抛 `ScenarioDraftError`（编辑前就要明确告知）。
 */
export function parseDefinitionToDraft(definition: ScenarioDefinition): ScenarioDraft {
  const root = asRecord(definition)
  if (root === null) {
    throw new ScenarioDraftError('场景 JSON 根节点必须是对象（{...}）')
  }
  const version = definitionSchemaVersion(root as ScenarioDefinition)
  if (version === 0) {
    throw new ScenarioDraftError('schema_version 只能是 1 或 2（当前无法识别；请检查 JSON 原文）')
  }
  const meta = asRecord(root.meta) ?? {}
  const player = asRecord(root.player) ?? {}
  const goal = asRecord(root.goal)
  const objectives = goal !== null && Array.isArray(goal.objectives) ? goal.objectives : []
  return {
    schemaVersion: version,
    slug: asString(root.slug, 'my-first-scene'),
    meta: {
      title: asString(meta.title),
      author: asString(meta.author),
      description: asString(meta.description),
      tagsText: joinListText(Array.isArray(meta.tags) ? meta.tags : []),
    },
    player: {
      name: asString(player.name, '旅人'),
      hp: asNumberText(player.hp) === '' ? '20' : asNumberText(player.hp),
      maxHp: asNumberText(player.max_hp) === '' ? '20' : asNumberText(player.max_hp),
      location: asString(player.location),
      inventoryText: joinListText(Array.isArray(player.inventory) ? player.inventory : []),
    },
    rooms:
      Array.isArray(root.rooms) && root.rooms.length > 0
        ? root.rooms.map((room, index) => parseRoom(room, index))
        : [emptyRoomDraft(1)],
    items: Array.isArray(root.items) ? root.items.map((item, index) => parseItem(item, index)) : [],
    npcs: Array.isArray(root.npcs) ? root.npcs.map((npc, index) => parseNpc(npc, index)) : [],
    flags: Array.isArray(root.flags) ? root.flags.map((flag) => parseFlag(flag)) : [],
    goal:
      goal === null
        ? { enabled: false, title: '完成目标', description: '', objectives: [] }
        : {
            enabled: true,
            title: asString(goal.title),
            description: asString(goal.description),
            objectives: objectives.map((objective, index) => parseObjective(objective, index)),
          },
    endings: Array.isArray(root.endings)
      ? root.endings.map((ending, index) => parseEnding(ending, index))
      : [],
    suggestions: Array.isArray(root.suggestions)
      ? root.suggestions.map((suggestion) => parseSuggestion(suggestion))
      : [],
  }
}

/** 载入 JSON 文本 → 草稿（JSON 语法/根节点错误抛 `ScenarioDraftError`）。 */
export function parseDraftFromJsonText(text: string): ScenarioDraft {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new ScenarioDraftError('JSON 语法错误：无法解析，请检查括号/引号/逗号')
  }
  const record = asRecord(parsed)
  if (record === null) {
    throw new ScenarioDraftError('场景 JSON 根节点必须是对象（{...}）')
  }
  return parseDefinitionToDraft(record)
}

/** `when` 是否为对象（编辑器保存前提示用；空对象/null 视为无）。 */
export function hasCondition(condition: ScenarioCondition | null): boolean {
  return condition !== null && Object.keys(condition).length > 0
}

/** 条件节点的类型（非「恰好一个键」返回空串）。 */
export function conditionKind(condition: ScenarioCondition | null): ScenarioConditionKind | '' {
  if (condition === null) {
    return ''
  }
  const keys = Object.keys(condition)
  if (keys.length !== 1) {
    return ''
  }
  const key = keys[0] ?? ''
  return (SCENARIO_CONDITION_KINDS as readonly ScenarioConditionKindOption[]).some(
    (option) => option.kind === key,
  )
    ? (key as ScenarioConditionKind)
    : ''
}

/** 条件节点条件树节点数（组合子递归计数；防御 UI 里的大条件）。 */
export function conditionNodeCount(condition: ScenarioCondition | null): number {
  if (condition === null) {
    return 0
  }
  const kind = conditionKind(condition)
  if (kind === 'all' || kind === 'any') {
    const children = condition[kind]
    const list = Array.isArray(children) ? children : []
    return 1 + list.reduce<number>((total, child) => total + conditionNodeCount(asCondition(child)), 0)
  }
  if (kind === 'not') {
    return 1 + conditionNodeCount(asCondition(condition.not))
  }
  return 1
}

/** 新建一个条件节点骨架（编辑器下拉切换时用；参数为占位值，作者按需改）。 */
export function createCondition(kind: ScenarioConditionKind): ScenarioCondition {
  switch (kind) {
    case 'in_room':
      return { in_room: 'entry' }
    case 'has_item':
      return { has_item: 'item_1' }
    case 'has_item_tag':
      return { has_item_tag: 'quest' }
    case 'npc_state':
      return { npc_state: { npc: 'npc_1', state: 'briefed' } }
    case 'hp_lte':
      return { hp_lte: 6 }
    case 'hp_gte':
      return { hp_gte: 12 }
    case 'flag':
      return { flag: 'my_flag' }
    case 'objective_done':
      return { objective_done: 'objective_1' }
    case 'has_item_tag_count':
      return { has_item_tag_count: { tag: 'quest', at_least: 1 } }
    case 'item_in_room':
      return { item_in_room: { item: 'item_1', room: 'entry' } }
    case 'npc_in_room':
      return { npc_in_room: { npc: 'npc_1', room: 'entry' } }
    case 'turn_gte':
      return { turn_gte: 1 }
    case 'turn_lte':
      return { turn_lte: 10 }
    case 'world_time_gte':
      return { world_time_gte: 6 }
    case 'world_time_lte':
      return { world_time_lte: 22 }
    case 'all':
      return { all: [{ in_room: 'entry' }] }
    case 'any':
      return { any: [{ in_room: 'entry' }] }
    case 'not':
      return { not: { in_room: 'entry' } }
    case 'always':
      return { always: true }
  }
}

/**
 * 保存幂等键：同一份 JSON 手动重试复用同键；内容变化得到新键（避免同键异参 409）。
 * 用 FNV-1a 32 位（足够区分编辑会话内的内容差异，不做安全哈希）。
 */
export function scenarioDraftHash(text: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash.toString(16).padStart(8, '0')
}

/** 编辑器保存动作名（scene-editor 前缀 + 内容哈希；供 store 生成/清理幂等键）。 */
export function scenarioEditorSaveAction(text: string): string {
  return `scenario-editor-save:${scenarioDraftHash(text)}`
}
