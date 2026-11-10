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
  CharacterCardDetailResponse,
  CharacterCardImportResponse,
  CharacterCardListResponse,
  CharacterCardValidateResponse,
  CreateGameRequest,
  CreatedGameResponse,
  GameListResponse,
  GamePlayerViewResponse,
  GameViewResponse,
  ReplayResponse,
  TurnRequest,
  TurnResult,
} from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

/** 单请求默认超时（毫秒）：LLM 叙事回合可能较慢，给足时间但避免永久卡死 busy。 */
export const DEFAULT_TIMEOUT_MS = 120_000

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
  /** 单请求超时毫秒数（缺省 `DEFAULT_TIMEOUT_MS`）。 */
  timeoutMs?: number
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
  // 超时 + 外部取消：任一触发都 abort；避免网络挂起导致 busy 永久为 true（R2 包 C）。
  const controller = new AbortController()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const forwardAbort = (): void => controller.abort()
  options.signal?.addEventListener('abort', forwardAbort, { once: true })
  try {
    let response: Response
    try {
      response = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: controller.signal })
    } catch (error) {
      if (error instanceof DOMException && (error.name === 'AbortError' || error.name === 'TimeoutError')) {
        throw new ApiClientError(0, 'TIMEOUT', '请求超时或已取消，请重试')
      }
      throw error
    }
    let body: unknown
    try {
      body = response.status === 204 ? null : await response.json()
    } catch {
      throw new ApiClientError(response.status, 'INTERNAL', `HTTP ${response.status}：响应不是合法 JSON`)
    }
    if (!response.ok) {
      throw parseErrorEnvelope(body, response.status)
    }
    return body as T
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', forwardAbort)
  }
}

const CREATED_GAME_KEYS = ['game_id', 'turn', 'version', 'snapshot_version', 'save_seed'] as const
const GAME_VIEW_KEYS = [
  'game_id',
  'turn',
  'version',
  'snapshot_version',
  'status',
  'last_text',
  'scenario',
  'ending_text',
] as const
const PLAYER_VIEW_KEYS = [
  'game_id',
  'turn',
  'version',
  'snapshot_version',
  'status',
  'scenario',
  'hp',
  'max_hp',
  'inventory',
  'room',
  'goal',
  'ending_text',
  'suggested_actions',
] as const
const GAME_LIST_KEYS = ['games', 'total'] as const
const GAME_LIST_ITEM_KEYS = [
  'game_id',
  'turn',
  'version',
  'snapshot_version',
  'status',
  'scenario',
  'last_text',
  'updated_at',
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

/** `GET /api/v1/games` 查询参数：`limit` 1..50（默认 10）、`offset` ≥0。 */
export interface GameListQuery {
  limit?: number
  offset?: number
}

/** `GET /api/v1/games`：存档列表（只读；D9 续玩 UI 数据源，按最近更新时间倒序）。 */
export async function listGames(
  query: GameListQuery = {},
  signal?: AbortSignal,
): Promise<GameListResponse> {
  const params = new URLSearchParams()
  if (query.limit !== undefined) {
    params.set('limit', String(query.limit))
  }
  if (query.offset !== undefined) {
    params.set('offset', String(query.offset))
  }
  const queryString = params.toString()
  const suffix = queryString === '' ? '' : `?${queryString}`
  const payload = await request<unknown>(`/api/v1/games${suffix}`, { method: 'GET' }, { signal })
  const record = assertRecord(payload, 'GameListResponse')
  assertKeys(record, 'GameListResponse', GAME_LIST_KEYS)
  if (!Array.isArray(record.games) || typeof record.total !== 'number') {
    throw contractError('GameListResponse', 'games/total 类型不符')
  }
  for (const item of record.games) {
    const game = assertRecord(item, 'GameListItemResponse')
    assertKeys(game, 'GameListItemResponse', GAME_LIST_ITEM_KEYS)
    if (typeof game.game_id !== 'string' || typeof game.scenario !== 'string') {
      throw contractError('GameListItemResponse', 'game_id/scenario 类型不符')
    }
  }
  return record as unknown as GameListResponse
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

// ---------------------------------------------------------------------------
// M6-026 人物卡库（character-cards 7 端点；generate 留 M6-028）
// ---------------------------------------------------------------------------

const CARD_KEYS = [
  'schema_version',
  'name',
  'attributes',
  'hp',
  'starting_items',
  'background',
  'appearance',
  'source',
  'generated_by',
] as const
const CARD_SUMMARY_KEYS = ['card_id', 'name', 'source', 'generated_by', 'updated_at'] as const
const CARD_LIST_KEYS = ['cards', 'total'] as const
const CARD_DETAIL_KEYS = ['card'] as const
const CARD_IMPORT_KEYS = ['card_id', 'card', 'warnings'] as const
const CARD_VALIDATE_KEYS = ['valid', 'errors'] as const
const CARD_VALIDATION_ERROR_KEYS = ['loc', 'type', 'msg'] as const

/** 人物卡 9 字段精确校验（`extra="forbid"`；缺失或多字段即契约漂移）。 */
function assertCardRecord(record: Record<string, unknown>, label: string): void {
  assertKeys(record, label, CARD_KEYS)
  if (typeof record.name !== 'string' || typeof record.hp !== 'number') {
    throw contractError(label, 'name/hp 类型不符')
  }
}

export interface CardListQuery {
  query?: string
  limit?: number
  offset?: number
}

/** `GET /api/v1/character-cards`：卡库列表 `{cards, total}`（只读；limit 1..100、offset≥0）。 */
export async function listCards(
  query: CardListQuery = {},
  signal?: AbortSignal,
): Promise<CharacterCardListResponse> {
  const params = new URLSearchParams()
  if (query.query !== undefined && query.query !== '') {
    params.set('query', query.query)
  }
  if (query.limit !== undefined) {
    params.set('limit', String(query.limit))
  }
  if (query.offset !== undefined) {
    params.set('offset', String(query.offset))
  }
  const queryString = params.toString()
  const suffix = queryString === '' ? '' : `?${queryString}`
  const payload = await request<unknown>(
    `/api/v1/character-cards${suffix}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'CharacterCardListResponse')
  assertKeys(record, 'CharacterCardListResponse', CARD_LIST_KEYS)
  if (!Array.isArray(record.cards) || typeof record.total !== 'number') {
    throw contractError('CharacterCardListResponse', 'cards/total 类型不符')
  }
  for (const item of record.cards) {
    const summary = assertRecord(item, 'CharacterCardListItemResponse')
    assertKeys(summary, 'CharacterCardListItemResponse', CARD_SUMMARY_KEYS)
  }
  return record as unknown as CharacterCardListResponse
}

/** `GET /api/v1/character-cards/{card_id}`：卡片详情 `{card}`（未知 404 / ID 非法 422）。 */
export async function getCard(
  cardId: string,
  signal?: AbortSignal,
): Promise<CharacterCardDetailResponse> {
  const payload = await request<unknown>(
    `/api/v1/character-cards/${encodeURIComponent(cardId)}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'CharacterCardDetailResponse')
  assertKeys(record, 'CharacterCardDetailResponse', CARD_DETAIL_KEYS)
  assertCardRecord(assertRecord(record.card, 'CharacterCard'), 'CharacterCard')
  return record as unknown as CharacterCardDetailResponse
}

/** `GET /api/v1/character-cards/schema`：JSON Schema 对象本身（不套信封；只读）。 */
export async function getCardSchema(signal?: AbortSignal): Promise<Record<string, unknown>> {
  const payload = await request<unknown>(
    '/api/v1/character-cards/schema',
    { method: 'GET' },
    { signal },
  )
  return assertRecord(payload, 'CharacterCardSchema')
}

/** `POST /api/v1/character-cards/validate`：查询式校验 `{valid, errors}`（只读无副作用）。 */
export async function validateCard(
  payload: unknown,
  signal?: AbortSignal,
): Promise<CharacterCardValidateResponse> {
  const response = await request<unknown>(
    '/api/v1/character-cards/validate',
    { method: 'POST', body: JSON.stringify(payload) },
    { signal },
  )
  const record = assertRecord(response, 'CharacterCardValidateResponse')
  assertKeys(record, 'CharacterCardValidateResponse', CARD_VALIDATE_KEYS)
  if (typeof record.valid !== 'boolean' || !Array.isArray(record.errors)) {
    throw contractError('CharacterCardValidateResponse', 'valid/errors 类型不符')
  }
  for (const item of record.errors) {
    const error = assertRecord(item, 'CharacterCardValidationErrorResponse')
    assertKeys(error, 'CharacterCardValidationErrorResponse', CARD_VALIDATION_ERROR_KEYS)
  }
  return record as unknown as CharacterCardValidateResponse
}

/**
 * `POST /api/v1/character-cards/import`：multipart（file 部件，≤64KB）+ 幂等键 → 201。
 * 不手写 `Content-Type`（浏览器/FormData 自动带 boundary；第 23 章 §23.5）。
 */
export async function importCard(
  content: string,
  actionKey: string,
  filename = 'card.json',
  signal?: AbortSignal,
): Promise<CharacterCardImportResponse> {
  const form = new FormData()
  form.append('file', new Blob([content], { type: 'application/json' }), filename)
  const payload = await request<unknown>(
    '/api/v1/character-cards/import',
    { method: 'POST', body: form },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'CharacterCardImportResponse')
  assertKeys(record, 'CharacterCardImportResponse', CARD_IMPORT_KEYS)
  assertCardRecord(assertRecord(record.card, 'CharacterCard'), 'CharacterCard')
  if (!Array.isArray(record.warnings)) {
    throw contractError('CharacterCardImportResponse', 'warnings 不是数组')
  }
  for (const item of record.warnings) {
    const warning = assertRecord(item, 'CharacterCardWarningResponse')
    if (typeof warning.code !== 'string' || typeof warning.message !== 'string') {
      throw contractError('CharacterCardWarningResponse', 'code/message 类型不符')
    }
  }
  return record as unknown as CharacterCardImportResponse
}
