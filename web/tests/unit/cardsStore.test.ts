import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useCardsStore } from '@/stores/cards'
import { useGameStore } from '@/stores/game'
import type { CharacterCard } from '@/api/types'

const CARD: CharacterCard = {
  schema_version: '1.0',
  name: '灰烬旅人',
  attributes: { str: 12, dex: 14, con: 11, int: 15, wis: 10, cha: 8, max_hp: 30 },
  hp: 30,
  starting_items: ['item.torch'],
  background: '在旧矿镇长大的拾荒者。',
  appearance: null,
  source: 'upload',
  generated_by: null,
}

const SUMMARY = {
  card_id: 'card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
  name: '灰烬旅人',
  source: 'upload',
  generated_by: null,
  updated_at: '2026-11-10T13:00:00Z',
}

const CREATED = { game_id: 'g1', turn: 1, version: 1, snapshot_version: 1, save_seed: '42' }
const PLAYER_VIEW = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  status: 'active',
  hp: 30,
  max_hp: 30,
  inventory: [],
  room: { room_id: 'entry', title: '入口', description: '石室。', exits: {}, items: [], npcs: [] },
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init))
  vi.stubGlobal('fetch', mock)
  return mock
}

function callOf(mock: ReturnType<typeof mockFetch>, index: number): [RequestInfo | URL, RequestInit | undefined] {
  return mock.mock.calls[index] as [RequestInfo | URL, RequestInit | undefined]
}

function headersOf(mock: ReturnType<typeof mockFetch>, index: number): Headers {
  return new Headers(callOf(mock, index)[1]?.headers)
}

function bodyOf(mock: ReturnType<typeof mockFetch>, index: number): string {
  return String(callOf(mock, index)[1]?.body)
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('cards store（列表/详情/校验/导入）', () => {
  it('loadList 空态：total=0、无错误（列表页空态来源）', async () => {
    const mock = mockFetch(() => jsonResponse(200, { cards: [], total: 0 }))
    const store = useCardsStore()

    await store.loadList()

    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
    expect(store.error).toBe('')
    expect(store.loading).toBe(false)
    expect(String(callOf(mock, 0)[0])).toContain('limit=20&offset=0')
  })

  it('分页：nextPage 用 offset=20，末页 canNext=false', async () => {
    let calls = 0
    const many = Array.from({ length: 20 }, (_, index) => ({ ...SUMMARY, card_id: `card_${index}` }))
    const mock = mockFetch(() => {
      calls += 1
      return jsonResponse(200, { cards: calls === 1 ? many : [SUMMARY], total: 21 })
    })
    const store = useCardsStore()

    await store.loadList()
    expect(store.canNext).toBe(true)

    await store.nextPage()
    expect(store.page).toBe(2)
    expect(store.canNext).toBe(false)
    expect(String(callOf(mock, 1)[0])).toContain('offset=20')
  })

  it('列表失败：错误进 store、items 清空（手动重试）', async () => {
    mockFetch(() =>
      jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'database error' }),
    )
    const store = useCardsStore()

    await store.loadList().catch(() => undefined)

    expect(store.items).toEqual([])
    expect(store.error).not.toBe('')
    expect(store.errorCode).toBe('INTERNAL')
  })

  it('详情 404：转中文错误提示，detail 为空', async () => {
    mockFetch(() =>
      jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '人物卡不存在: x' }),
    )
    const store = useCardsStore()

    await store.loadDetail('card_x').catch(() => undefined)

    expect(store.detail).toBeNull()
    expect(store.detailError).toContain('404')
    expect(store.detailErrorCode).toBe('NOT_FOUND')
  })

  it('validate：本地预检失败不发请求；服务端 errors 可展示', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        valid: false,
        errors: [{ loc: ['hp'], type: 'value_error', msg: 'hp must be <= attributes.max_hp' }],
      }),
    )
    const store = useCardsStore()

    await store.validate('{not json')
    expect(store.validateError).toContain('JSON 语法错误')
    expect(mock).not.toHaveBeenCalled()

    await store.validate(JSON.stringify(CARD))
    expect(store.validateResult?.valid).toBe(false)
    expect(store.validateResult?.errors[0]?.loc).toEqual(['hp'])
  })

  it('导入成功：multipart + 幂等键，返回 card_id 与 warnings', async () => {
    const mock = mockFetch(() =>
      jsonResponse(201, {
        card_id: SUMMARY.card_id,
        card: CARD,
        warnings: [{ code: 'SOURCE_OVERRIDDEN', message: 'source forced to upload', field: 'source' }],
      }),
    )
    const store = useCardsStore()

    const cardId = await store.importText(JSON.stringify(CARD))

    expect(cardId).toBe(SUMMARY.card_id)
    expect(store.importWarnings[0]?.code).toBe('SOURCE_OVERRIDDEN')
    expect(store.importError).toBe('')
    const [, init] = callOf(mock, 0)
    expect(init?.body).toBeInstanceOf(FormData)
    expect(headersOf(mock, 0).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('导入 409：清键后再次提交自动换新键（无自动重试）', async () => {
    let calls = 0
    const keys: Array<string | null> = []
    mockFetch((_url, init) => {
      calls += 1
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      if (calls === 1) {
        return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '同键异参' })
      }
      return jsonResponse(201, { card_id: SUMMARY.card_id, card: CARD, warnings: [] })
    })
    const store = useCardsStore()

    const first = await store.importText(JSON.stringify(CARD))
    expect(first).toBeNull()
    expect(store.importError).toContain('幂等冲突')
    expect(store.pendingImportKey).toBeNull()

    const second = await store.importText(JSON.stringify(CARD))
    expect(second).toBe(SUMMARY.card_id)
    expect(calls).toBe(2)
    expect(keys[1]).not.toBe(keys[0])
  })

  it('按卡开始游戏：POST /games body.card_id + 幂等键（详情页「开始游戏」）', async () => {
    const mock = mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW)
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })
    const game = useGameStore()

    const gameId = await game.createGame({ card_id: SUMMARY.card_id })

    expect(gameId).toBe('g1')
    expect(game.version).toBe(1)
    expect(JSON.parse(bodyOf(mock, 0))).toEqual({ card_id: SUMMARY.card_id })
    expect(headersOf(mock, 0).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
  })
})
