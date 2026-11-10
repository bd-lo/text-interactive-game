import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  CARD_GENERATE_TIMEOUT_MS,
  generateCard,
  saveCard,
} from '@/api/client'
import type { CharacterCard } from '@/api/types'

const CARD: CharacterCard = {
  schema_version: '1.0',
  name: '灰烬旅人',
  attributes: { str: 12, dex: 14, con: 11, int: 15, wis: 10, cha: 8, max_hp: 30 },
  hp: 30,
  starting_items: ['item.torch'],
  background: '在旧矿镇长大的拾荒者。',
  appearance: null,
  source: 'manual',
  generated_by: null,
}

const SAVED = { card_id: 'card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK', card: CARD }

function templateResponse() {
  return {
    draft: { ...CARD },
    validation: {
      valid: true,
      errors: [],
      warnings: [
        { code: 'TEMPLATE_FALLBACK', message: 'LLM 不可用；已使用确定性模板草稿', field: null },
      ],
    },
    generated_by: null,
  }
}

function llmResponse() {
  return {
    draft: { ...CARD, source: 'llm', generated_by: 'deepseek:chat' },
    validation: { valid: true, errors: [], warnings: [] },
    generated_by: 'deepseek:chat',
  }
}

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init))
  vi.stubGlobal('fetch', mock)
  return mock
}

function callOf(
  mock: ReturnType<typeof mockFetch>,
  index: number,
): [RequestInfo | URL, RequestInit | undefined] {
  return mock.mock.calls[index] as [RequestInfo | URL, RequestInit | undefined]
}

function headersOf(mock: ReturnType<typeof mockFetch>, index: number): Headers {
  return new Headers(callOf(mock, index)[1]?.headers)
}

function bodyOf(mock: ReturnType<typeof mockFetch>, index: number): unknown {
  return JSON.parse(String(callOf(mock, index)[1]?.body))
}

beforeEach(() => {
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('生成人物卡 API client（M6-028）', () => {
  it('generateCard：POST /character-cards/generate + 幂等键 + brief/options 请求体', async () => {
    const mock = mockFetch(() => jsonResponse(200, templateResponse()))

    const response = await generateCard(
      { brief: '旧矿镇的拾荒者', schema_version: '1.0', options: { tone: 'grim' } },
      'card-generate',
    )

    expect(response.draft.name).toBe('灰烬旅人')
    expect(response.generated_by).toBeNull()
    expect(response.validation.warnings?.[0]?.code).toBe('TEMPLATE_FALLBACK')
    const [url, init] = callOf(mock, 0)
    expect(String(url)).toBe('/api/v1/character-cards/generate')
    expect(init?.method).toBe('POST')
    expect(bodyOf(mock, 0)).toEqual({
      brief: '旧矿镇的拾荒者',
      schema_version: '1.0',
      options: { tone: 'grim' },
    })
    expect(headersOf(mock, 0).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(CARD_GENERATE_TIMEOUT_MS).toBe(60_000)
  })

  it('generateCard：LLM 草稿响应（generated_by 非 null）原样解析', async () => {
    mockFetch(() => jsonResponse(200, llmResponse()))

    const response = await generateCard({ brief: '旧矿镇拾荒者' }, 'card-generate')

    expect(response.generated_by).toBe('deepseek:chat')
    expect(response.draft.source).toBe('llm')
    expect(response.validation.warnings).toEqual([])
  })

  it('generateCard：同 actionKey 的手动重试复用同一 Idempotency-Key', async () => {
    const mock = mockFetch(() => jsonResponse(200, templateResponse()))

    await generateCard({ brief: '旧矿镇拾荒者' }, 'card-generate')
    await generateCard({ brief: '旧矿镇拾荒者' }, 'card-generate')

    expect(headersOf(mock, 0).get('Idempotency-Key')).toBe(
      headersOf(mock, 1).get('Idempotency-Key'),
    )
  })

  it('generateCard：响应字段漂移（缺 generated_by）→ 契约错误', async () => {
    const broken = templateResponse()
    delete (broken as Record<string, unknown>).generated_by
    mockFetch(() => jsonResponse(200, broken))

    await expect(generateCard({ brief: 'x' }, 'card-generate')).rejects.toThrow(/契约不符/)
  })

  it('generateCard：草稿缺 9 字段之一（background）→ 契约错误', async () => {
    const broken = templateResponse()
    delete (broken.draft as unknown as Record<string, unknown>).background
    mockFetch(() => jsonResponse(200, broken))

    await expect(generateCard({ brief: 'x' }, 'card-generate')).rejects.toThrow(/契约不符/)
  })

  it('generateCard：validation 字段漂移（缺 warnings）→ 契约错误', async () => {
    const broken = templateResponse()
    delete (broken.validation as Record<string, unknown>).warnings
    mockFetch(() => jsonResponse(200, broken))

    await expect(generateCard({ brief: 'x' }, 'card-generate')).rejects.toThrow(/契约不符/)
  })

  it('generateCard：429 解析 Retry-After → ApiClientError.retryAfterSeconds', async () => {
    mockFetch(() =>
      jsonResponse(
        429,
        { ok: false, error_code: 'OUT_OF_RANGE', message: '人物卡生成请求过于频繁' },
        { 'Retry-After': '30' },
      ),
    )

    const error = (await generateCard({ brief: 'x' }, 'card-generate').catch(
      (reason: unknown) => reason,
    )) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(429)
    expect(error.code).toBe('OUT_OF_RANGE')
    expect(error.retryAfterSeconds).toBe(30)
  })

  it('generateCard：409 映射为 isConflict（供 store 清键）', async () => {
    mockFetch(() =>
      jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '同键异参' }),
    )

    const error = (await generateCard({ brief: 'x' }, 'card-generate').catch(
      (reason: unknown) => reason,
    )) as ApiClientError

    expect(error.isConflict).toBe(true)
    expect(error.retryAfterSeconds).toBe(0)
  })

  it('saveCard：POST /character-cards + 9 字段 body + 幂等键 → 201 {card_id, card}', async () => {
    const mock = mockFetch(() => jsonResponse(201, SAVED))

    const response = await saveCard(CARD, 'card-save')

    expect(response.card_id).toBe(SAVED.card_id)
    expect(response.card.name).toBe('灰烬旅人')
    const [url, init] = callOf(mock, 0)
    expect(String(url)).toBe('/api/v1/character-cards')
    expect(init?.method).toBe('POST')
    expect(bodyOf(mock, 0)).toEqual(CARD)
    expect(headersOf(mock, 0).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('saveCard：响应多个字段 → 契约错误（extra=forbid 对齐）', async () => {
    mockFetch(() => jsonResponse(201, { ...SAVED, deleted: false }))

    await expect(saveCard(CARD, 'card-save')).rejects.toThrow(/契约不符/)
  })
})
