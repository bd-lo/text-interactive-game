/**
 * 前端契约类型快照（M6-025）。
 *
 * 唯一来源是 live OpenAPI：`create_app().openapi()`（D-M6-2 / 第 23 章 §23.3）。
 * 字段集合由 `tests/integration/test_frontend_contract.py` 静态比对；后端契约变更未同步
 * 到本文件时契约测试会失败。M6-025 只使用现有 4 个端点，replay/character-cards 等
 * M6 后端前置交付后再做加法扩展。
 */

/** 前端实际调用的端点（必须是 live OpenAPI 的子集，契约测试逐项断言）。 */
export const API_PATHS = {
  createGame: '/api/v1/games',
  getGame: '/api/v1/games/{game_id}',
  getPlayerView: '/api/v1/games/{game_id}/view',
  submitTurn: '/api/v1/turns',
} as const

export type Scenario = 'm2' | 'm3' | 'm4'
export type GameStatus = 'active' | 'ended'

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

/** `POST /api/v1/games` 请求体（`CreateGameBody`；响应 5 字段，别名为 `save_seed`）。 */
export interface CreateGameRequest {
  seed?: number
  scenario?: Scenario
}

/** `POST /api/v1/games` 201 响应（`CreatedGameResponse`，5 字段）。 */
export interface CreatedGameResponse {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  save_seed: string
}

/** `GET /api/v1/games/{game_id}` 200 响应（`GameViewResponse`，刷新/断线恢复）。 */
export interface GameViewResponse {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  status: GameStatus
  last_text: string | null
}

/** 实体引用（`ItemRefResponse`）。 */
export interface ItemRef {
  item_id: string
  name: string
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
  items: ItemRef[]
  npcs: NpcRef[]
}

/** `GET /api/v1/games/{game_id}/view` 200 响应（`GamePlayerViewResponse`，StatusPanel 唯一数据源）。 */
export interface GamePlayerViewResponse {
  game_id: string
  turn: number
  version: number
  snapshot_version: number
  status: GameStatus
  hp: number
  max_hp: number
  inventory: ItemRef[]
  room: RoomView
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

/** `POST /api/v1/turns` 200 响应（`TurnResult`；业务失败也是 200 + `ok=false`）。 */
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
}

/** `POST /api/v1/turns` 请求体（JSON key 为 `input`）。 */
export interface TurnRequest {
  game_id: string
  input: string
  expected_version: number
}

/** 失败统一信封（第 6 章 §6.7；`ok:false` + 稳定错误码）。 */
export interface ApiErrorEnvelope {
  ok: false
  error_code: string
  message: string
  violations?: unknown
}
