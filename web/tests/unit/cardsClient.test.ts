import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClientError, getCard, importCard, listCards, validateCard } from '@/api/client'
import type { CharacterCard } from '@/api/types'

const CARD: CharacterCard = {
  schema_version: '1.0',
  name: '灰烬旅人',
  attributes: { str: 12, dex: 14, con: 11, int: 15, wis: 10, cha: 8, max_hp: 30 },
  hp: 30,
  starting_items: ['item.torch', 'item.potion'],
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

function firstCall(mock: ReturnType<typeof mockFetch>): [RequestInfo | URL, RequestInit | undefined] {
  return mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('卡库 API client（M6-026）', () => {
  it('listCards 组装 query/limit/offset 并校验列表字段', async () => {
    const mock = mockFetch(() => jsonResponse(200, { cards: [SUMMARY], total: 1 }))

    const page = await listCards({ query: '灰烬', limit: 20, offset: 40 })

    expect(page.total).toBe(1)
    expect(page.cards[0]?.card_id).toBe(SUMMARY.card_id)
    const [url] = firstCall(mock)
    expect(String(url)).toBe(
      '/api/v1/character-cards?query=%E7%81%B0%E7%83%AC&limit=20&offset=40',
    )
  })

  it('getCard 校验 9 字段并返回 {card}', async () => {
    const mock = mockFetch(() => jsonResponse(200, { card: CARD }))

    const detail = await getCard('card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK')

    expect(detail.card.name).toBe('灰烬旅人')
    expect(String(firstCall(mock)[0])).toBe('/api/v1/character-cards/card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK')
  })

  it('validateCard 以 JSON POST 并解析 {valid, errors}', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        valid: false,
        errors: [{ loc: ['hp'], type: 'value_error', msg: 'hp must be <= attributes.max_hp' }],
      }),
    )

    const result = await validateCard({ name: 'x' })

    expect(result.valid).toBe(false)
    expect(result.errors[0]?.loc).toEqual(['hp'])
    const [, init] = firstCall(mock)
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(init?.body))).toEqual({ name: 'x' })
  })

  it('importCard 用 multipart FormData + 幂等键，不手写 Content-Type', async () => {
    const mock = mockFetch(() =>
      jsonResponse(201, {
        card_id: SUMMARY.card_id,
        card: CARD,
        warnings: [{ code: 'SOURCE_OVERRIDDEN', message: 'source forced to upload', field: 'source' }],
      }),
    )

    const response = await importCard('{"name":"灰烬旅人"}', 'card-import')

    expect(response.card_id).toBe(SUMMARY.card_id)
    expect(response.warnings[0]?.code).toBe('SOURCE_OVERRIDDEN')
    const [, init] = firstCall(mock)
    expect(init?.body).toBeInstanceOf(FormData)
    const form = init?.body as FormData
    const file = form.get('file')
    expect(file).not.toBeNull()
    expect((file as File).name).toBe('card.json')
    const headers = new Headers(init?.headers)
    expect(headers.get('Idempotency-Key')).toBe('card-import')
    expect(headers.get('Content-Type')).toBeNull() // 由 FormData 自动带 boundary
  })

  it('失败信封 → ApiClientError（413 状态保留，供错误映射）', async () => {
    mockFetch(() =>
      jsonResponse(413, { ok: false, error_code: 'INVALID_INPUT', message: '人物卡 JSON 超过 64KB 上限' }),
    )

    const error = (await importCard('{}', 'card-import').catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(413)
    expect(error.code).toBe('INVALID_INPUT')
  })
})
