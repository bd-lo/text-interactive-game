/**
 * API client（M6-025 + M6-027）：只调现有 5 个端点（含只读 replay），无自动重试、无 SSE。
 *
 * 错误契约（第 6 章 §6.7、D-M6-6③）：业务失败仍是 HTTP 200 + `TurnResult(ok=false)`；
 * 协议错误走 4xx/5xx 统一失败信封 `{ok:false, error_code, message, violations?}`。
 * 关键响应先做运行时字段校验再返回（第 23 章 §23.3「不得裸 body as T」）。
 */

import { getOrCreate } from '@/utils/idempotency'

import type {
  ApiErrorEnvelope,
  CreateGameRequest,
  CreatedGameResponse,
  GamePlayerViewResponse,
  GameViewResponse,
  ReplayResponse,
  TurnRequest,
  TurnResult,
} from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string
  readonly violations: unknown

  constructor(status: number, code: string, message: string, violations?: unknown) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.code = code
    this.violations = violations
  }

  get isConflict(): boolean {
    return this.status === 409 || this.code === 'CONFLICT'
  }
}

export interface RequestOptions {
  /** 写请求：附带 Idempotency-Key（同键手动重试复用）。 */
  idempotent?: boolean
  /** 逻辑动作标识；与 sessionStorage 中的键一一对应。 */
  actionKey?: string
  signal?: AbortSignal
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function contractError(label: string, detail: string): ApiClientError {
  return new ApiClientError(200, 'INTERNAL', `${label} 响应与前端契约不符：${detail}`)
}

function assertRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw contractError(label, '不是 JSON 对象')
  }
  return value
}

/** 顶部字段集合精确比对（后端 `extra="forbid"`，漂移即失败）。 */
function assertKeys(record: Record<string, unknown>, label: string, expected: readonly string[]): void {
  const actual = Object.keys(record).sort()
  const wanted = [...expected].sort()
  const same = actual.length === wanted.length && actual.every((key, index) => key === wanted[index])
  if (!same) {
    throw contractError(label, `字段集合为 [${actual.join(', ')}]，期望 [${wanted.join(', ')}]`)
  }
}

function parseErrorEnvelope(body: unknown, status: number): ApiClientError {
  if (isRecord(body) && body.ok === false) {
    const envelope = body as unknown as ApiErrorEnvelope
    const code = typeof envelope.error_code === 'string' ? envelope.error_code : 'INTERNAL'
    const message = typeof envelope.message === 'string' ? envelope.message : `HTTP ${status}`
    return new ApiClientError(status, code, message, envelope.violations)
  }
  return new ApiClientError(status, 'INTERNAL', `HTTP ${status}`)
}

async function request<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (options.idempotent === true && options.actionKey !== undefined) {
    headers.set('Idempotency-Key', getOrCreate(options.actionKey))
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: options.signal })
  const body: unknown = response.status === 204 ? null : await response.json()
  if (!response.ok) {
    throw parseErrorEnvelope(body, response.status)
  }
  return body as T
}

const CREATED_GAME_KEYS = ['game_id', 'turn', 'version', 'snapshot_version', 'save_seed'] as const
const GAME_VIEW_KEYS = ['game_id', 'turn', 'version', 'snapshot_version', 'status', 'last_text'] as const
const PLAYER_VIEW_KEYS = [
  'game_id',
  'turn',
  'version',
  'snapshot_version',
  'status',
  'hp',
  'max_hp',
  'inventory',
  'room',
] as const
const TURN_RESULT_KEYS = [
  'ok',
  'turn_id',
  'version',
  'text',
  'events',
  'error_code',
  'effects',
  'suggested_actions',
  'usage',
] as const

/** `POST /api/v1/games`：创建对局（写，必带幂等键）。 */
export async function createGame(
  body: CreateGameRequest,
  actionKey: string,
  signal?: AbortSignal,
): Promise<CreatedGameResponse> {
  const payload = await request<unknown>(
    '/api/v1/games',
    { method: 'POST', body: JSON.stringify(body) },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'CreatedGameResponse')
  assertKeys(record, 'CreatedGameResponse', CREATED_GAME_KEYS)
  return record as unknown as CreatedGameResponse
}

/** `GET /api/v1/games/{game_id}`：刷新/断线恢复版本链（只读）。 */
export async function getGame(gameId: string, signal?: AbortSignal): Promise<GameViewResponse> {
  const payload = await request<unknown>(
    `/api/v1/games/${encodeURIComponent(gameId)}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'GameViewResponse')
  assertKeys(record, 'GameViewResponse', GAME_VIEW_KEYS)
  return record as unknown as GameViewResponse
}

/** `GET /api/v1/games/{game_id}/view`：状态面板唯一数据源（只读、零事件）。 */
export async function getPlayerView(gameId: string, signal?: AbortSignal): Promise<GamePlayerViewResponse> {
  const payload = await request<unknown>(
    `/api/v1/games/${encodeURIComponent(gameId)}/view`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'GamePlayerViewResponse')
  assertKeys(record, 'GamePlayerViewResponse', PLAYER_VIEW_KEYS)
  return record as unknown as GamePlayerViewResponse
}

const REPLAY_KEYS = ['events', 'from_seq', 'to_seq'] as const
const REPLAY_EVENT_KEYS = [
  'id',
  'seq',
  'turn_id',
  'actor_id',
  'method',
  'event_type',
  'params',
  'result',
  'rng_seed',
  'ts',
] as const

/** replay 端点查询参数：`from_seq` ≥1（默认 1）、`limit` 1..500（默认 200）。 */
export interface ReplayQuery {
  fromSeq?: number
  limit?: number
}

/** `GET /api/v1/games/{game_id}/replay`：只读事件切片（D-M6-3），无幂等键（只读）。 */
export async function getReplay(
  gameId: string,
  query: ReplayQuery = {},
  signal?: AbortSignal,
): Promise<ReplayResponse> {
  const params = new URLSearchParams()
  if (query.fromSeq !== undefined) {
    params.set('from_seq', String(query.fromSeq))
  }
  if (query.limit !== undefined) {
    params.set('limit', String(query.limit))
  }
  const queryString = params.toString()
  const suffix = queryString === '' ? '' : `?${queryString}`
  const payload = await request<unknown>(
    `/api/v1/games/${encodeURIComponent(gameId)}/replay${suffix}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'ReplayResponse')
  assertKeys(record, 'ReplayResponse', REPLAY_KEYS)
  if (!Array.isArray(record.events)) {
    throw contractError('ReplayResponse', 'events 不是数组')
  }
  for (const item of record.events) {
    const event = assertRecord(item, 'ReplayEventResponse')
    assertKeys(event, 'ReplayEventResponse', REPLAY_EVENT_KEYS)
    if (typeof event.seq !== 'number' || typeof event.turn_id !== 'number') {
      throw contractError('ReplayEventResponse', 'seq/turn_id 类型不符')
    }
  }
  return record as unknown as ReplayResponse
}

/** `POST /api/v1/turns`：提交回合（写，必带幂等键）；业务失败返回 `ok=false` 不抛异常。 */
export async function submitTurn(
  body: TurnRequest,
  actionKey: string,
  signal?: AbortSignal,
): Promise<TurnResult> {
  const payload = await request<unknown>(
    '/api/v1/turns',
    { method: 'POST', body: JSON.stringify(body) },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'TurnResult')
  assertKeys(record, 'TurnResult', TURN_RESULT_KEYS)
  if (typeof record.ok !== 'boolean' || typeof record.version !== 'number' || !Array.isArray(record.events)) {
    throw contractError('TurnResult', 'ok/version/events 类型不符')
  }
  return record as unknown as TurnResult
}
