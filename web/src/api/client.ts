/**
 * API client（M6-025 + M6-026 + M6-027 + M6-028 + H4 世界书 + 用户自上传场景）：
 * 无自动重试、无 SSE。
 *
 * 错误契约（第 6 章 §6.7、D-M6-6③）：业务失败仍是 HTTP 200 + `TurnResult(ok=false)`；
 * 协议错误走 4xx/5xx 统一失败信封 `{ok:false, error_code, message, violations?}`。
 * 限流 429 的 `Retry-After` 头解析进 `ApiClientError.retryAfterSeconds`（第 23 章 §23.6）。
 * 关键响应先做运行时字段校验再返回（第 23 章 §23.3「不得裸 body as T」）。
 */

import { getOrCreate } from '@/utils/idempotency'

import type {
  ApiErrorEnvelope,
  CardGenerateRequest,
  CardGenerateResponse,
  CharacterCard,
  CharacterCardDetailResponse,
  CharacterCardImportResponse,
  CharacterCardListResponse,
  CharacterCardSaveResponse,
  CharacterCardValidateResponse,
  CreateGameRequest,
  CreatedGameResponse,
  GameListResponse,
  GamePlayerViewResponse,
  GameViewResponse,
  LlmConnectionTest,
  LlmSettingsClearResponse,
  LlmSettingsSaveRequest,
  LlmSettingsSaveResponse,
  LlmSettingsStatus,
  ReplayResponse,
  ScenarioDetailResponse,
  ScenarioImportResponse,
  ScenarioListResponse,
  ScenarioStats,
  ScenarioSummary,
  ScenarioValidateResponse,
  ScenarioWarning,
  TurnRequest,
  TurnResult,
  WorldBookDeleteResponse,
  WorldBookDetailResponse,
  WorldBookImportResponse,
  WorldBookListResponse,
  WorldBookValidateResponse,
} from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

/** 单请求默认超时（毫秒）：LLM 叙事回合可能较慢，给足时间但避免永久卡死 busy。 */
export const DEFAULT_TIMEOUT_MS = 120_000

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string
  readonly violations: unknown
  /** 429 响应的 `Retry-After` 秒数（无该头/非法值时为 0；第 23 章 §23.6）。 */
  readonly retryAfterSeconds: number

  constructor(
    status: number,
    code: string,
    message: string,
    violations?: unknown,
    retryAfterSeconds = 0,
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.code = code
    this.violations = violations
    this.retryAfterSeconds = retryAfterSeconds
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

/** 解析 `Retry-After`（服务端只发「秒」形式；HTTP-date 等非法值按 0 处理）。 */
function parseRetryAfter(value: string | null): number {
  if (value === null) {
    return 0
  }
  const seconds = Number.parseInt(value.trim(), 10)
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 0
}

function parseErrorEnvelope(body: unknown, status: number, retryAfterSeconds = 0): ApiClientError {
  if (isRecord(body) && body.ok === false) {
    const envelope = body as unknown as ApiErrorEnvelope
    const code = typeof envelope.error_code === 'string' ? envelope.error_code : 'INTERNAL'
    const message = typeof envelope.message === 'string' ? envelope.message : `HTTP ${status}`
    return new ApiClientError(status, code, message, envelope.violations, retryAfterSeconds)
  }
  return new ApiClientError(status, 'INTERNAL', `HTTP ${status}`, undefined, retryAfterSeconds)
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
      throw parseErrorEnvelope(
        body,
        response.status,
        parseRetryAfter(response.headers.get('Retry-After')),
      )
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
  'world_book',
  'world_book_id',
  'lore',
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
  'world_book',
  'lore',
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
  'narrative_source',
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

// ---------------------------------------------------------------------------
// M6-028 生成人物卡（generate 草稿 + 保存到卡库；第 23 章 §23.6）
// ---------------------------------------------------------------------------

/** 生成请求超时：服务端 LLM 任务 timeout_s=20 + 一次修复，前端给足 60s（`RequestOptions.timeoutMs`）。 */
export const CARD_GENERATE_TIMEOUT_MS = 60_000

const CARD_GENERATE_KEYS = ['draft', 'validation', 'generated_by'] as const
const CARD_GENERATE_VALIDATION_KEYS = ['valid', 'errors', 'warnings'] as const
const CARD_SAVE_KEYS = ['card_id', 'card'] as const

/** 生成草稿校验错误列表 `[{loc,type,msg}]` 逐项校验（Pydantic `extra="forbid"`）。 */
function assertCardValidationErrors(value: unknown, label: string): void {
  if (!Array.isArray(value)) {
    throw contractError(label, 'errors 不是数组')
  }
  for (const item of value) {
    assertKeys(
      assertRecord(item, 'CharacterCardValidationErrorResponse'),
      'CharacterCardValidationErrorResponse',
      CARD_VALIDATION_ERROR_KEYS,
    )
  }
}

/** 告警列表 `[{code,message,field?}]`：code/message 必填，field 可缺省。 */
function assertCardWarnings(value: unknown, label: string): void {
  if (!Array.isArray(value)) {
    throw contractError(label, 'warnings 不是数组')
  }
  for (const item of value) {
    const warning = assertRecord(item, 'CharacterCardWarningResponse')
    if (typeof warning.code !== 'string' || typeof warning.message !== 'string') {
      throw contractError('CharacterCardWarningResponse', 'code/message 类型不符')
    }
  }
}

/**
 * `POST /api/v1/character-cards/generate`：按 `brief` 生成人物卡草稿（不保存/不建局）。
 *
 * - **写幂等键**：`Idempotency-Key` 由 `actionKey` 派生，手动重试复用同一键（无自动重试）；
 * - 响应精确校验 `{draft, validation, generated_by}`、`validation` 的 `valid/errors/warnings`
 *   与 `draft` 的 9 字段（后端 `extra="forbid"`；漂移即失败）；
 * - 默认离线：服务端返回确定性模板兜底（`generated_by=null` + `TEMPLATE_FALLBACK` warning）；
 * - 限流 429（`OUT_OF_RANGE` + `Retry-After`）由 `ApiClientError.retryAfterSeconds` 携带。
 */
export async function generateCard(
  body: CardGenerateRequest,
  actionKey: string,
  signal?: AbortSignal,
): Promise<CardGenerateResponse> {
  const payload = await request<unknown>(
    '/api/v1/character-cards/generate',
    { method: 'POST', body: JSON.stringify(body) },
    { idempotent: true, actionKey, signal, timeoutMs: CARD_GENERATE_TIMEOUT_MS },
  )
  const record = assertRecord(payload, 'CardGenerateResponse')
  assertKeys(record, 'CardGenerateResponse', CARD_GENERATE_KEYS)
  assertCardRecord(assertRecord(record.draft, 'CharacterCard'), 'CharacterCard')
  const validation = assertRecord(record.validation, 'CardGenerateValidation')
  assertKeys(validation, 'CardGenerateValidation', CARD_GENERATE_VALIDATION_KEYS)
  if (typeof validation.valid !== 'boolean') {
    throw contractError('CardGenerateValidation', 'valid 类型不符')
  }
  assertCardValidationErrors(validation.errors, 'CardGenerateValidation')
  assertCardWarnings(validation.warnings, 'CardGenerateValidation')
  if (record.generated_by !== null && typeof record.generated_by !== 'string') {
    throw contractError('CardGenerateResponse', 'generated_by 应为字符串或 null')
  }
  return record as unknown as CardGenerateResponse
}

/**
 * `POST /api/v1/character-cards`：保存已校验人物卡 → 201 `{card_id, card}`（写幂等键）。
 *
 * 保存前**禁止**自动建局/跳转（第 23 章 §23.6-6）；同键异参由服务端 409，前端清键后手动重试。
 */
export async function saveCard(
  card: CharacterCard,
  actionKey: string,
  signal?: AbortSignal,
): Promise<CharacterCardSaveResponse> {
  const payload = await request<unknown>(
    '/api/v1/character-cards',
    { method: 'POST', body: JSON.stringify(card) },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'CharacterCardSaveResponse')
  assertKeys(record, 'CharacterCardSaveResponse', CARD_SAVE_KEYS)
  if (typeof record.card_id !== 'string' || record.card_id === '') {
    throw contractError('CharacterCardSaveResponse', 'card_id 应为非空字符串')
  }
  assertCardRecord(assertRecord(record.card, 'CharacterCard'), 'CharacterCard')
  return record as unknown as CharacterCardSaveResponse
}

// ---------------------------------------------------------------------------
// H4 世界书（world-books 5 端点；H4-CONTRACT §2/§7）
// 口径：只做资料库 CRUD + 只读校验；世界书不参与事件溯源（不产生 Event、不改状态）。
// ---------------------------------------------------------------------------

const WORLD_BOOK_LIST_KEYS = ['world_books', 'total'] as const
const WORLD_BOOK_SUMMARY_KEYS = [
  'world_book_id',
  'name',
  'description',
  'source',
  'entry_count',
  'updated_at',
] as const
const WORLD_BOOK_DETAIL_KEYS = ['world_book', 'entries'] as const
const WORLD_BOOK_DETAIL_BODY_KEYS = [
  'world_book_id',
  'name',
  'description',
  'source',
  'entry_count',
  'updated_at',
] as const
const WORLD_BOOK_ENTRY_KEYS = [
  'entry_uid',
  'keys',
  'secondary_keys',
  'content',
  'enabled',
  'constant',
  'order',
  'position',
  'depth',
  'selective',
  'selective_logic',
] as const
const WORLD_BOOK_IMPORT_KEYS = ['world_book_id', 'name', 'entry_count', 'source', 'warnings'] as const
const WORLD_BOOK_VALIDATE_KEYS = ['valid', 'errors', 'warnings', 'preview'] as const
const WORLD_BOOK_VALIDATE_PREVIEW_KEYS = ['name', 'entry_count', 'detected_format'] as const
const WORLD_BOOK_VALIDATION_ERROR_KEYS = ['loc', 'type', 'msg'] as const
const WORLD_BOOK_DELETE_KEYS = ['world_book_id', 'deleted'] as const

/** 世界书告警项 `[{code,message}]`：不阻塞导入，逐项校验 code/message 为字符串。 */
function assertWorldBookWarnings(value: unknown, label: string): void {
  if (!Array.isArray(value)) {
    throw contractError(label, 'warnings 不是数组')
  }
  for (const item of value) {
    const warning = assertRecord(item, 'WorldBookWarningResponse')
    if (typeof warning.code !== 'string' || typeof warning.message !== 'string') {
      throw contractError('WorldBookWarningResponse', 'code/message 类型不符')
    }
  }
}

/** 世界书校验错误项 `[{loc,type,msg}]` 逐项校验。 */
function assertWorldBookValidationErrors(value: unknown, label: string): void {
  if (!Array.isArray(value)) {
    throw contractError(label, 'errors 不是数组')
  }
  for (const item of value) {
    assertKeys(
      assertRecord(item, 'WorldBookValidationErrorResponse'),
      'WorldBookValidationErrorResponse',
      WORLD_BOOK_VALIDATION_ERROR_KEYS,
    )
  }
}

/** 列表项逐项校验（`extra="forbid"` 契约；字段漂移即失败）。 */
function assertWorldBookSummary(item: unknown, label: string): void {
  const summary = assertRecord(item, label)
  assertKeys(summary, label, WORLD_BOOK_SUMMARY_KEYS)
  if (typeof summary.world_book_id !== 'string' || typeof summary.entry_count !== 'number') {
    throw contractError(label, 'world_book_id/entry_count 类型不符')
  }
}

/** `GET /api/v1/world-books` 查询参数：`query`（匹配 name）、`limit` 1..100、`offset` ≥0。 */
export interface WorldBookListQuery {
  query?: string
  limit?: number
  offset?: number
}

/** `GET /api/v1/world-books`：世界书列表 `{world_books, total}`（只读；按 updated_at 倒序）。 */
export async function listWorldBooks(
  query: WorldBookListQuery = {},
  signal?: AbortSignal,
): Promise<WorldBookListResponse> {
  const params = new URLSearchParams()
  if (query.query !== undefined && query.query.trim() !== '') {
    params.set('query', query.query.trim())
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
    `/api/v1/world-books${suffix}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'WorldBookListResponse')
  assertKeys(record, 'WorldBookListResponse', WORLD_BOOK_LIST_KEYS)
  if (!Array.isArray(record.world_books) || typeof record.total !== 'number') {
    throw contractError('WorldBookListResponse', 'world_books/total 类型不符')
  }
  for (const item of record.world_books) {
    assertWorldBookSummary(item, 'WorldBookSummaryResponse')
  }
  return record as unknown as WorldBookListResponse
}

/** `GET /api/v1/world-books/{world_book_id}`：详情 `{world_book, entries}`（未知/已删 404）。 */
export async function getWorldBook(
  worldBookId: string,
  signal?: AbortSignal,
): Promise<WorldBookDetailResponse> {
  const payload = await request<unknown>(
    `/api/v1/world-books/${encodeURIComponent(worldBookId)}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'WorldBookDetailResponse')
  assertKeys(record, 'WorldBookDetailResponse', WORLD_BOOK_DETAIL_KEYS)
  assertKeys(
    assertRecord(record.world_book, 'WorldBookDetailBodyResponse'),
    'WorldBookDetailBodyResponse',
    WORLD_BOOK_DETAIL_BODY_KEYS,
  )
  if (!Array.isArray(record.entries)) {
    throw contractError('WorldBookDetailResponse', 'entries 不是数组')
  }
  for (const item of record.entries) {
    const entry = assertRecord(item, 'WorldBookEntryResponse')
    assertKeys(entry, 'WorldBookEntryResponse', WORLD_BOOK_ENTRY_KEYS)
    if (typeof entry.entry_uid !== 'number' || !Array.isArray(entry.keys)) {
      throw contractError('WorldBookEntryResponse', 'entry_uid/keys 类型不符')
    }
  }
  return record as unknown as WorldBookDetailResponse
}

/**
 * `POST /api/v1/world-books/import`：multipart（`file` 部件 ≤256KB + 可选 `name`）+ 幂等键 → 201。
 * 不手写 `Content-Type`（FormData 自动带 boundary；第 23 章 §23.5）。
 */
export async function importWorldBook(
  content: string,
  actionKey: string,
  options: { filename?: string; name?: string } = {},
  signal?: AbortSignal,
): Promise<WorldBookImportResponse> {
  const form = new FormData()
  form.append(
    'file',
    new Blob([content], { type: 'application/json' }),
    options.filename ?? 'world-book.json',
  )
  const name = options.name?.trim()
  if (name !== undefined && name !== '') {
    form.append('name', name)
  }
  const payload = await request<unknown>(
    '/api/v1/world-books/import',
    { method: 'POST', body: form },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'WorldBookImportResponse')
  assertKeys(record, 'WorldBookImportResponse', WORLD_BOOK_IMPORT_KEYS)
  if (typeof record.world_book_id !== 'string' || typeof record.entry_count !== 'number') {
    throw contractError('WorldBookImportResponse', 'world_book_id/entry_count 类型不符')
  }
  assertWorldBookWarnings(record.warnings, 'WorldBookImportResponse')
  return record as unknown as WorldBookImportResponse
}

/** `POST /api/v1/world-books/validate`：只读校验 `{payload}` → `{valid, errors, warnings, preview}`。 */
export async function validateWorldBook(
  payload: unknown,
  signal?: AbortSignal,
): Promise<WorldBookValidateResponse> {
  const response = await request<unknown>(
    '/api/v1/world-books/validate',
    { method: 'POST', body: JSON.stringify({ payload }) },
    { signal },
  )
  const record = assertRecord(response, 'WorldBookValidateResponse')
  assertKeys(record, 'WorldBookValidateResponse', WORLD_BOOK_VALIDATE_KEYS)
  if (typeof record.valid !== 'boolean') {
    throw contractError('WorldBookValidateResponse', 'valid 类型不符')
  }
  assertWorldBookValidationErrors(record.errors, 'WorldBookValidateResponse')
  assertWorldBookWarnings(record.warnings, 'WorldBookValidateResponse')
  const preview = assertRecord(record.preview, 'WorldBookValidatePreviewResponse')
  assertKeys(preview, 'WorldBookValidatePreviewResponse', WORLD_BOOK_VALIDATE_PREVIEW_KEYS)
  return record as unknown as WorldBookValidateResponse
}

/** `DELETE /api/v1/world-books/{world_book_id}`：软删（幂等；必带幂等键）→ `{world_book_id, deleted}`。 */
export async function deleteWorldBook(
  worldBookId: string,
  actionKey: string,
  signal?: AbortSignal,
): Promise<WorldBookDeleteResponse> {
  const payload = await request<unknown>(
    `/api/v1/world-books/${encodeURIComponent(worldBookId)}`,
    { method: 'DELETE' },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'WorldBookDeleteResponse')
  assertKeys(record, 'WorldBookDeleteResponse', WORLD_BOOK_DELETE_KEYS)
  if (typeof record.world_book_id !== 'string' || typeof record.deleted !== 'boolean') {
    throw contractError('WorldBookDeleteResponse', 'world_book_id/deleted 类型不符')
  }
  return record as unknown as WorldBookDeleteResponse
}

// ---------------------------------------------------------------------------
// 玩家自上传场景（scenarios 5 端点；docs/design/user-scenario-modules.md §2/§3.4）
// 口径：场景 = 玩家上传的 JSON 定义（房间/物品/NPC/目标/结局），导入即玩；
// 定义原文由服务端校验并存库，前端只做基础预检与只读展示，不做字段级校验。
// ---------------------------------------------------------------------------

const SCENARIO_LIST_KEYS = ['scenarios', 'total'] as const
const SCENARIO_STATS_KEYS = [
  'rooms',
  'items',
  'npcs',
  'objectives',
  'endings',
  'suggestions',
] as const
const SCENARIO_SUMMARY_KEYS = [
  'scenario_id',
  'slug',
  'title',
  'author',
  'description',
  'schema_version',
  'created_at',
  'summary',
  'warnings',
] as const
const SCENARIO_DETAIL_KEYS = [
  'scenario_id',
  'slug',
  'title',
  'author',
  'description',
  'schema_version',
  'created_at',
  'definition',
  'summary',
  'warnings',
] as const
const SCENARIO_VALIDATE_KEYS = ['ok', 'errors', 'warnings', 'summary'] as const
const SCENARIO_VALIDATION_ERROR_KEYS = ['path', 'message'] as const
const SCENARIO_IMPORT_KEYS = ['scenario_id', 'slug', 'title', 'summary', 'warnings'] as const

/**
 * 场景告警 `[{path,message}]` → 归一化 `ScenarioWarning[]`（与校验错误同构）。
 *
 * 场景设计文档只约定「warnings 不阻断」，元素形状由后端契约定为 `{path,message}`；
 * 这里对纯字符串（归一为 `path=''`）与缺 `path` 的对象也容错，避免警告形状差异炸掉整个页面
 * （警告本身不承担阻断职责，展示按 path+message）。
 */
function parseScenarioWarnings(value: unknown, label: string): ScenarioWarning[] {
  if (!Array.isArray(value)) {
    throw contractError(label, 'warnings 不是数组')
  }
  const warnings: ScenarioWarning[] = []
  for (const item of value) {
    if (typeof item === 'string') {
      warnings.push({ path: '', message: item })
      continue
    }
    const warning = assertRecord(item, 'ScenarioWarningResponse')
    if (typeof warning.message !== 'string') {
      throw contractError('ScenarioWarningResponse', 'message 类型不符')
    }
    warnings.push({
      path: typeof warning.path === 'string' ? warning.path : '',
      message: warning.message,
    })
  }
  return warnings
}

/** 场景统计 `{rooms,items,npcs,objectives,endings,suggestions}` 6 个计数逐项校验。 */
function parseScenarioStats(value: unknown, label: string): ScenarioStats {
  const record = assertRecord(value, label)
  assertKeys(record, label, SCENARIO_STATS_KEYS)
  for (const key of SCENARIO_STATS_KEYS) {
    if (typeof record[key] !== 'number') {
      throw contractError(label, `${key} 类型不符`)
    }
  }
  return record as unknown as ScenarioStats
}

/** 列表项（`ScenarioSummaryResponse`）逐项校验并归一化 warnings。 */
function parseScenarioSummary(item: unknown, label: string): ScenarioSummary {
  const record = assertRecord(item, label)
  assertKeys(record, label, SCENARIO_SUMMARY_KEYS)
  if (
    typeof record.scenario_id !== 'string' ||
    typeof record.slug !== 'string' ||
    typeof record.title !== 'string' ||
    typeof record.author !== 'string' ||
    typeof record.description !== 'string' ||
    typeof record.schema_version !== 'number' ||
    typeof record.created_at !== 'string'
  ) {
    throw contractError(label, 'scenario_id/slug/title/author/description/schema_version/created_at 类型不符')
  }
  return {
    ...record,
    summary: parseScenarioStats(record.summary, `${label}.summary`),
    warnings: parseScenarioWarnings(record.warnings, `${label}.warnings`),
  } as unknown as ScenarioSummary
}

/** `GET /api/v1/scenarios` 查询参数：`q` 搜索（标题/作者）、`limit` 1..100、`offset` ≥0。 */
export interface ScenarioListQuery {
  q?: string
  limit?: number
  offset?: number
}

/** `GET /api/v1/scenarios`：场景库列表 `{scenarios, total}`（只读；按 created_at 倒序）。 */
export async function listScenarios(
  query: ScenarioListQuery = {},
  signal?: AbortSignal,
): Promise<ScenarioListResponse> {
  const params = new URLSearchParams()
  const search = query.q?.trim()
  if (search !== undefined && search !== '') {
    params.set('q', search)
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
    `/api/v1/scenarios${suffix}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'ScenarioListResponse')
  assertKeys(record, 'ScenarioListResponse', SCENARIO_LIST_KEYS)
  if (!Array.isArray(record.scenarios) || typeof record.total !== 'number') {
    throw contractError('ScenarioListResponse', 'scenarios/total 类型不符')
  }
  return {
    scenarios: record.scenarios.map((item) =>
      parseScenarioSummary(item, 'ScenarioSummaryResponse'),
    ),
    total: record.total,
  }
}

/** `GET /api/v1/scenarios/{scenario_id}`：详情（含原始定义、summary、warnings；未知/已删 404）。 */
export async function getScenario(
  scenarioId: string,
  signal?: AbortSignal,
): Promise<ScenarioDetailResponse> {
  const payload = await request<unknown>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}`,
    { method: 'GET' },
    { signal },
  )
  const record = assertRecord(payload, 'ScenarioDetailResponse')
  assertKeys(record, 'ScenarioDetailResponse', SCENARIO_DETAIL_KEYS)
  if (
    typeof record.scenario_id !== 'string' ||
    typeof record.slug !== 'string' ||
    typeof record.title !== 'string' ||
    typeof record.author !== 'string' ||
    typeof record.description !== 'string' ||
    typeof record.schema_version !== 'number' ||
    typeof record.created_at !== 'string'
  ) {
    throw contractError('ScenarioDetailResponse', '场景基础字段类型不符')
  }
  assertRecord(record.definition, 'ScenarioDetailResponse.definition')
  return {
    ...record,
    summary: parseScenarioStats(record.summary, 'ScenarioDetailResponse.summary'),
    warnings: parseScenarioWarnings(record.warnings, 'ScenarioDetailResponse.warnings'),
  } as unknown as ScenarioDetailResponse
}

/**
 * `POST /api/v1/scenarios/validate`：只读预检（body=场景 JSON 原文，无幂等键、不写库）。
 *
 * 与人物卡/世界书 validate 不同，这里**不套 `{payload}`**：请求体就是场景 JSON 本身
 * （用户内容平台 v1 契约，`docs/design/user-scenario-modules.md` §3.4）。
 */
export async function validateScenario(
  scenario: unknown,
  signal?: AbortSignal,
): Promise<ScenarioValidateResponse> {
  const payload = await request<unknown>(
    '/api/v1/scenarios/validate',
    { method: 'POST', body: JSON.stringify(scenario) },
    { signal },
  )
  const record = assertRecord(payload, 'ScenarioValidateResponse')
  assertKeys(record, 'ScenarioValidateResponse', SCENARIO_VALIDATE_KEYS)
  if (typeof record.ok !== 'boolean' || !Array.isArray(record.errors)) {
    throw contractError('ScenarioValidateResponse', 'ok/errors 类型不符')
  }
  for (const item of record.errors) {
    const error = assertRecord(item, 'ScenarioValidationErrorResponse')
    assertKeys(error, 'ScenarioValidationErrorResponse', SCENARIO_VALIDATION_ERROR_KEYS)
    if (typeof error.path !== 'string' || typeof error.message !== 'string') {
      throw contractError('ScenarioValidationErrorResponse', 'path/message 类型不符')
    }
  }
  return {
    ok: record.ok,
    errors: record.errors as ScenarioValidateResponse['errors'],
    warnings: parseScenarioWarnings(record.warnings, 'ScenarioValidateResponse.warnings'),
    summary: parseScenarioStats(record.summary, 'ScenarioValidateResponse.summary'),
  }
}

/**
 * `POST /api/v1/scenarios`：multipart（`file` 部件 ≤256KB，文件名 `*.json`）+ 幂等键 → 201。
 * 不手写 `Content-Type`（FormData 自动带 boundary；第 23 章 §23.5）。
 */
export async function importScenario(
  content: string,
  actionKey: string,
  filename = 'scenario.json',
  signal?: AbortSignal,
): Promise<ScenarioImportResponse> {
  const form = new FormData()
  form.append('file', new Blob([content], { type: 'application/json' }), filename)
  const payload = await request<unknown>(
    '/api/v1/scenarios',
    { method: 'POST', body: form },
    { idempotent: true, actionKey, signal },
  )
  const record = assertRecord(payload, 'ScenarioImportResponse')
  assertKeys(record, 'ScenarioImportResponse', SCENARIO_IMPORT_KEYS)
  if (
    typeof record.scenario_id !== 'string' ||
    typeof record.slug !== 'string' ||
    typeof record.title !== 'string'
  ) {
    throw contractError('ScenarioImportResponse', 'scenario_id/slug/title 类型不符')
  }
  return {
    ...record,
    summary: parseScenarioStats(record.summary, 'ScenarioImportResponse.summary'),
    warnings: parseScenarioWarnings(record.warnings, 'ScenarioImportResponse.warnings'),
  } as unknown as ScenarioImportResponse
}

/** `DELETE /api/v1/scenarios/{scenario_id}`：软删 204（不影响已开局存档；重复删除 404，必带幂等键）。 */
export async function deleteScenario(
  scenarioId: string,
  actionKey: string,
  signal?: AbortSignal,
): Promise<void> {
  await request<unknown>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}`,
    { method: 'DELETE' },
    { idempotent: true, actionKey, signal },
  )
}

// ---------------------------------------------------------------------------
// LLM 运行期设置（玩家在网页里输入/测试/清除自己的 DeepSeek API Key）
// 契约：GET/PUT/DELETE /api/v1/settings/llm + POST /api/v1/settings/llm/test。
// 安全口径：只读状态只含打码 key；前端不持久化明文（表单保存后立即清空密码框）。
// 写端点按值幂等、契约不要求 Idempotency-Key（不做自动重试）。
// ---------------------------------------------------------------------------

const LLM_STATUS_KEYS = [
  'configured',
  'enabled',
  'source',
  'provider',
  'model',
  'api_base',
  'masked_key',
  'has_saved_user_key',
  'detail',
] as const
const LLM_TEST_KEYS = ['ok', 'latency_ms', 'provider', 'model', 'error'] as const
const LLM_SAVE_KEYS = ['status', 'test'] as const
const LLM_CLEAR_KEYS = ['status'] as const

/** 当前 LLM 配置状态逐字段校验（`extra="forbid"`；漂移即契约错误）。 */
function parseLlmStatus(value: unknown, label = 'LlmStatusResponse'): LlmSettingsStatus {
  const record = assertRecord(value, label)
  assertKeys(record, label, LLM_STATUS_KEYS)
  if (
    typeof record.configured !== 'boolean' ||
    typeof record.enabled !== 'boolean' ||
    typeof record.source !== 'string' ||
    typeof record.provider !== 'string' ||
    typeof record.model !== 'string' ||
    typeof record.api_base !== 'string' ||
    typeof record.has_saved_user_key !== 'boolean' ||
    typeof record.detail !== 'string' ||
    (record.masked_key !== null && typeof record.masked_key !== 'string')
  ) {
    throw contractError(label, '状态字段类型不符')
  }
  return record as unknown as LlmSettingsStatus
}

/** 连接测试结果逐字段校验（`latency_ms`/`error` 可为 null）。 */
function parseLlmTest(value: unknown, label = 'LlmTestResponse'): LlmConnectionTest {
  const record = assertRecord(value, label)
  assertKeys(record, label, LLM_TEST_KEYS)
  if (
    typeof record.ok !== 'boolean' ||
    typeof record.provider !== 'string' ||
    typeof record.model !== 'string' ||
    (record.latency_ms !== null && typeof record.latency_ms !== 'number') ||
    (record.error !== null && typeof record.error !== 'string')
  ) {
    throw contractError(label, '测试结果字段类型不符')
  }
  return record as unknown as LlmConnectionTest
}

/** `GET /api/v1/settings/llm`：当前 LLM 配置状态（只读、零网络、不含明文密钥）。 */
export async function getLlmSettings(signal?: AbortSignal): Promise<LlmSettingsStatus> {
  const payload = await request<unknown>('/api/v1/settings/llm', { method: 'GET' }, { signal })
  return parseLlmStatus(payload)
}

/**
 * `PUT /api/v1/settings/llm`：保存本机 key 并热切换（当前进程立即生效）。
 *
 * body 必带完整 `api_key`（前端不持久化/不回显）；`run_test=true` 时服务端保存后立即
 * 测试一次（失败不影响已保存配置）。写端点按值幂等、不带 Idempotency-Key。
 */
export async function saveLlmSettings(
  body: LlmSettingsSaveRequest,
  signal?: AbortSignal,
): Promise<LlmSettingsSaveResponse> {
  const payload = await request<unknown>(
    '/api/v1/settings/llm',
    { method: 'PUT', body: JSON.stringify(body) },
    { signal },
  )
  const record = assertRecord(payload, 'LlmSaveResponse')
  assertKeys(record, 'LlmSaveResponse', LLM_SAVE_KEYS)
  const status = parseLlmStatus(record.status, 'LlmSaveResponse.status')
  const test = record.test === null ? null : parseLlmTest(record.test, 'LlmSaveResponse.test')
  return { status, test }
}

/** `POST /api/v1/settings/llm/test`：用当前生效配置发一次最小请求（默认离线时零网络）。 */
export async function testLlmConnection(signal?: AbortSignal): Promise<LlmConnectionTest> {
  const payload = await request<unknown>(
    '/api/v1/settings/llm/test',
    { method: 'POST' },
    { signal },
  )
  return parseLlmTest(payload)
}

/** `DELETE /api/v1/settings/llm`：清除本机保存的 key 并热切换 → `{status}`。 */
export async function clearLlmSettings(signal?: AbortSignal): Promise<LlmSettingsClearResponse> {
  const payload = await request<unknown>(
    '/api/v1/settings/llm',
    { method: 'DELETE' },
    { signal },
  )
  const record = assertRecord(payload, 'LlmDeleteResponse')
  assertKeys(record, 'LlmDeleteResponse', LLM_CLEAR_KEYS)
  return { status: parseLlmStatus(record.status, 'LlmDeleteResponse.status') }
}
