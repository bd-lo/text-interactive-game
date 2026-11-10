/**
 * 前端契约类型快照（M6-025 骨架 + M6-027 回放 + M6-026 卡库导入 + M6-028 生成）。
 *
 * 唯一来源是 live OpenAPI：`create_app().openapi()`（D-M6-2 / 第 23 章 §23.3）。
 * 字段集合由 `tests/integration/test_frontend_contract.py` 静态比对；后端契约变更未同步
 * 到本文件时契约测试会失败。当前使用现有 11 个端点（games/turns/replay + character-cards 8 个）；
 * M6-028 generate 端点已接入（API client `generateCard`）。
 */

/** 前端实际调用的端点（必须是 live OpenAPI 的子集，契约测试逐项断言）。 */
export const API_PATHS = {
  createGame: '/api/v1/games',
  listGames: '/api/v1/games',
  getGame: '/api/v1/games/{game_id}',
  getPlayerView: '/api/v1/games/{game_id}/view',
  getReplay: '/api/v1/games/{game_id}/replay',
  submitTurn: '/api/v1/turns',
  listCards: '/api/v1/character-cards',
  saveCard: '/api/v1/character-cards',
  cardSchema: '/api/v1/character-cards/schema',
  importCard: '/api/v1/character-cards/import',
  validateCard: '/api/v1/character-cards/validate',
  generateCard: '/api/v1/character-cards/generate',
  getCard: '/api/v1/character-cards/{card_id}',
  deleteCard: '/api/v1/character-cards/{card_id}',
  listWorldBooks: '/api/v1/world-books',
  importWorldBook: '/api/v1/world-books/import',
  validateWorldBook: '/api/v1/world-books/validate',
  getWorldBook: '/api/v1/world-books/{world_book_id}',
  deleteWorldBook: '/api/v1/world-books/{world_book_id}',
} as const

export type Scenario = 'm2' | 'm3' | 'm4' | 'story'
export type GameStatus = 'active' | 'ended'

/** 人物卡来源（第 5 章 §5.7；import 端点强制为 upload）。 */
export type CardSource = 'upload' | 'llm' | 'manual'
/** 人物卡属性键（恰好 7 个；`max_hp` 范围 [1,100]，其余 [-10,100]）。 */
export type AttributeKey = 'str' | 'dex' | 'con' | 'int' | 'wis' | 'cha' | 'max_hp'

/** 第 5 章 §5.2 的 8 个稳定错误码。 */
export type ErrorCode =
  | 'INVALID_INPUT'
  | 'NOT_FOUND'
  | 'NOT_ALLOWED'
  | 'OUT_OF_RANGE'
  | 'CONFLICT'
  | 'SCHEMA_VIOLATION'
  | 'LLM_UNAVAILABLE'
  | 'INTERNAL'

/** `POST /api/v1/games` 请求体（`CreateGameBody`；响应 5 字段，别名为 `save_seed`）。
 *  `card_id` 为 D-M6-2 加法语义（与 `seed`/`scenario` 互斥）；`world_book_id` 为 H4 加法
 *  （可与其他字段组合，绑定世界书，未知 404）。
 */
export interface CreateGameRequest {
  card_id?: string
  seed?: number
  scenario?: Scenario
  world_book_id?: string
}

/** `POST /api/v1/games` 201 响应（`CreatedGameResponse`，5 字段）。 */
export interface CreatedGameResponse {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  save_seed: string
}

/** `GET /api/v1/games/{game_id}` 200 响应（`GameViewResponse`，刷新/断线恢复）。
 *  D9 加法字段：`scenario` 场景名；`ending_text` 结局正文（旧场景/未结束为 `null`）。
 *  H4 加法字段：`world_book`/`world_book_id`/`lore` 与 `/view` 同源（旧场景全为 `null`；
 *  刷新/续玩路径不再因未知响应键失败）。
 */
export interface GameViewResponse {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  status: GameStatus
  last_text: string | null
  scenario?: string
  ending_text?: string | null
  world_book?: WorldBookRef | null
  world_book_id?: string | null
  lore?: LoreView | null
}

/** 实体引用（`ItemRefResponse`）。 */
export interface ItemRef {
  item_id: string
  name: string
}

/** 玩家背包物品（`InventoryItemResponse`）：同模板实例聚合计数 `qty`（M6 加法字段）。 */
export interface InventoryItem {
  item_id: string
  name: string
  qty: number
}

/** NPC 引用（`NpcRefResponse`）。 */
export interface NpcRef {
  npc_id: string
  name: string
}

/** 玩家所在房间视图（`RoomViewResponse`）。 */
export interface RoomView {
  room_id: string
  title: string
  description: string
  exits: Record<string, string>
  /** D9 加法字段：方向 → 目标房间展示名（前端渲染「北 → 地窖」，不暴露 ID）。 */
  exit_names?: Record<string, string>
  items: ItemRef[]
  npcs: NpcRef[]
}

/** 剧情目标的一条子目标（`GoalObjectiveResponse`，D9 加法字段）。 */
export interface GoalObjective {
  id: string
  text: string
  done: boolean
}

/** 剧情目标视图（`GoalResponse`；仅 story 场景非空，旧场景 `goal = null`）。 */
export interface Goal {
  title: string
  description: string
  objectives: GoalObjective[]
}

/** 本局绑定的世界书摘要（`WorldBookRefResponse`，H4 加法字段；未绑定为 `null`）。 */
export interface WorldBookRef {
  world_book_id: string
  name: string
  entry_count: number
}

/** 本回合激活的一条世界书条目（`LoreEntryResponse`，只读）。 */
export interface LoreEntry {
  entry_uid: number
  content: string
}

/** `view.lore`：本回合激活的世界书条目（无世界书为 `null`）。 */
export interface LoreView {
  active_entries: LoreEntry[]
}

/** `GET /api/v1/games/{game_id}/view` 200 响应（`GamePlayerViewResponse`，StatusPanel 唯一数据源）。
 *  D9 加法字段：`scenario` 场景名、`goal` 剧情目标、`ending_text` 结局正文、
 *  `suggested_actions` 当前可执行的下一步指令（成功回合也给，供新手引导/可点建议）；
 *  H4 加法字段：`world_book` 本局绑定的世界书摘要、`lore` 本回合激活条目。
 */
export interface GamePlayerViewResponse {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  status: GameStatus
  scenario?: string
  hp: number
  max_hp: number
  inventory: InventoryItem[]
  room: RoomView
  goal?: Goal | null
  ending_text?: string | null
  suggested_actions?: string[]
  world_book?: WorldBookRef | null
  lore?: LoreView | null
}

/** `GET /api/v1/games` 列表项（`GameListItemResponse`，D9 存档列表/续玩）。 */
export interface GameListItem {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  status: GameStatus
  scenario: string
  last_text?: string | null
  updated_at?: string | null
}

/** `GET /api/v1/games` 200 响应 `{games, total}`（按最近更新时间倒序）。 */
export interface GameListResponse {
  games: GameListItem[]
  total: number
}

/** 事件记录（`Event`；M6-025 只读展示用，前端不据此推演权威状态）。 */
export interface EventRecord {
  id?: string | null
  turn_id: number
  seq?: number | null
  actor_id: string
  method: string
  event_type?: string | null
  params: Record<string, unknown>
  result: Record<string, unknown>
  rng_seed?: number | null
  ts?: string | null
}

/** `GET /api/v1/games/{game_id}/replay` 事件项（`ReplayEventResponse`，D-M6-3）。
 *  `result.narrative` 是文本回放的数据源；`params`/`result` 原样保留但 UI 不直接展示 params。
 */
export interface ReplayEvent {
  id: string
  seq: number
  turn_id: number
  actor_id: string
  method: string
  event_type: string | null
  params: Record<string, unknown>
  result: Record<string, unknown>
  rng_seed: number
  ts: string
}

/** `GET /api/v1/games/{game_id}/replay` 200 响应（`ReplayResponse`，按全局 `seq` 升序；只读零写入）。 */
export interface ReplayResponse {
  events: ReplayEvent[]
  from_seq: number
  to_seq: number
}

/** `POST /api/v1/turns` 200 响应（`TurnResult`；业务失败也是 200 + `ok=false`）。
 *  H4 加法字段：`narrative_source` 本回合叙事来源（`llm`/`template`/`null`），
 *  无事件回合也能标来源（前端徽章改读该字段）。
 */
export interface TurnResult {
  ok: boolean
  turn_id: number | null
  version: number
  text: string
  events: EventRecord[]
  error_code: ErrorCode | null
  effects: Array<Record<string, unknown>>
  suggested_actions: string[]
  usage: Record<string, unknown>
  narrative_source?: 'llm' | 'template' | null
}

/** `POST /api/v1/turns` 请求体（JSON key 为 `input`）。 */
export interface TurnRequest {
  game_id: string
  input: string
  expected_version: number
}

/** 人物卡 JSON 契约（`CharacterCard`，第 5 章 §5.7 / 第 23 章 §23.5；恰好 9 字段，不得增删）。 */
export interface CharacterCard {
  schema_version: '1.0'
  name: string
  attributes: Record<AttributeKey, number>
  hp: number
  starting_items: string[]
  background: string
  appearance: string | null
  source: CardSource
  generated_by: string | null
}

/** 卡库列表项（`CharacterCardListItemResponse`）。 */
export interface CharacterCardSummary {
  card_id: string
  name: string
  source: CardSource
  generated_by: string | null
  updated_at: string
}

/** `GET /api/v1/character-cards` 200 响应（`{cards, total}`，按 updated_at 倒序）。 */
export interface CharacterCardListResponse {
  cards: CharacterCardSummary[]
  total: number
}

/** `GET /api/v1/character-cards/{card_id}` 200 响应（`{card}`）。 */
export interface CharacterCardDetailResponse {
  card: CharacterCard
}

/** 导入告警项（`CharacterCardWarningResponse`；如 source 被强制、重复模板合并）。 */
export interface CharacterCardWarning {
  code: string
  message: string
  field?: string | null
}

/** `POST /api/v1/character-cards/import` 201 响应（multipart 上传）。 */
export interface CharacterCardImportResponse {
  card_id: string
  card: CharacterCard
  warnings: CharacterCardWarning[]
}

/** `POST /api/v1/character-cards`（保存）201 响应；M6-028 生成草稿的保存入口使用。 */
export interface CharacterCardSaveResponse {
  card_id: string
  card: CharacterCard
}

/** 查询式校验错误项（`{loc, type, msg}`）。 */
export interface CharacterCardValidationError {
  loc: string[]
  type: string
  msg: string
}

/** `POST /api/v1/character-cards/validate` 200 响应（只读无副作用）。 */
export interface CharacterCardValidateResponse {
  valid: boolean
  errors: CharacterCardValidationError[]
}

/** `POST /api/v1/character-cards/generate` 请求体（`CardGenerateBody`，M6-028）。 */
export interface CardGenerateRequest {
  brief: string
  schema_version?: '1.0'
  options?: Record<string, unknown>
}

/** 生成草稿的校验结果（`CardGenerateValidation`；errors/warnings 可缺省）。 */
export interface CardGenerateValidation {
  valid: boolean
  errors?: CharacterCardValidationError[]
  warnings?: CharacterCardWarning[]
}

/** `POST /api/v1/character-cards/generate` 200 响应（M6-028：草稿未保存，需再经 save/import）。 */
export interface CardGenerateResponse {
  draft: CharacterCard
  validation: CardGenerateValidation
  generated_by?: string | null
}

// ---------------------------------------------------------------------------
// H4 世界书（world-books；H4-CONTRACT §2/§7）
// 说明：格式兼容层只借鉴 SillyTavern 的数据结构与触发语义，UI 不参考其界面。
// ---------------------------------------------------------------------------

/** 世界书来源（`tavern` / `character_card` / `manual`；未知值回退原值展示）。 */
export type WorldBookSource = 'tavern' | 'character_card' | 'manual'

/** 世界书导入/校验告警项（`WorldBookWarningResponse`；不阻塞导入）。 */
export interface WorldBookWarning {
  code: string
  message: string
}

/** `GET /api/v1/world-books` 列表项（`WorldBookSummaryResponse`，按 updated_at 倒序）。 */
export interface WorldBookSummary {
  world_book_id: string
  name: string
  description: string
  source: string
  entry_count: number
  updated_at: string
}

/** `GET /api/v1/world-books` 200 响应 `{world_books, total}`。 */
export interface WorldBookListResponse {
  world_books: WorldBookSummary[]
  total: number
}

/** 世界书条目（`WorldBookEntryResponse`；触发语义字段与 H4-CONTRACT §2 一致）。 */
export interface WorldBookEntry {
  entry_uid: number
  keys: string[]
  secondary_keys: string[]
  content: string
  enabled: boolean
  constant: boolean
  order: number
  position: number
  depth: number
  selective: boolean
  selective_logic: number
}

/** 详情页世界书摘要（`WorldBookDetailBodyResponse`）。 */
export interface WorldBookDetailBody {
  world_book_id: string
  name: string
  description: string
  source: string
  entry_count: number
  updated_at: string
}

/** `GET /api/v1/world-books/{world_book_id}` 200 响应 `{world_book, entries}`。 */
export interface WorldBookDetailResponse {
  world_book: WorldBookDetailBody
  entries: WorldBookEntry[]
}

/** `POST /api/v1/world-books/import` 201 响应（multipart 上传）。 */
export interface WorldBookImportResponse {
  world_book_id: string
  name: string
  entry_count: number
  source: string
  warnings?: WorldBookWarning[]
}

/** 世界书校验错误项（`WorldBookValidationErrorResponse`，与人物卡校验同构）。 */
export interface WorldBookValidationError {
  loc: string[]
  type: string
  msg: string
}

/** 校验预览（`WorldBookValidatePreviewResponse`；格式不可识别时为占位值）。 */
export interface WorldBookValidatePreview {
  name?: string
  entry_count?: number
  detected_format?: string
}

/** `POST /api/v1/world-books/validate` 200 响应（只读无副作用）。 */
export interface WorldBookValidateResponse {
  valid: boolean
  errors?: WorldBookValidationError[]
  warnings?: WorldBookWarning[]
  preview?: WorldBookValidatePreview
}

/** `DELETE /api/v1/world-books/{world_book_id}` 200 响应（软删；幂等）。 */
export interface WorldBookDeleteResponse {
  world_book_id: string
  deleted: boolean
}

/** 失败统一信封（第 6 章 §6.7；`ok:false` + 稳定错误码）。 */
export interface ApiErrorEnvelope {
  ok: false
  error_code: string
  message: string
  violations?: unknown
}
