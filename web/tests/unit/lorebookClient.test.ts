import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  deleteWorldBook,
  getWorldBook,
  importWorldBook,
  listWorldBooks,
  validateWorldBook,
} from '@/api/client'

const SUMMARY = {
  world_book_id: 'wb_abc',
  name: '余烬哨站设定',
  description: '哨站与信标。',
  source: 'manual',
  entry_count: 2,
  updated_at: '2026-11-11T01:00:00Z',
}

const ENTRY = {
  entry_uid: 1,
  keys: ['哨站'],
  secondary_keys: [],
  content: '余烬哨站是河谷里的旧哨站。',
  enabled: true,
  constant: false,
  order: 10,
  position: 0,
  depth: 4,
  selective: false,
  selective_logic: 0,
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type FetchHandler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: FetchHandler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('世界书 API client（H4-CONTRACT §2）', () => {
  it('listWorldBooks：query/limit/offset 组装 + {world_books,total} 校验', async () => {
    const mock = mockFetch(() => jsonResponse(200, { world_books: [SUMMARY], total: 1 }))

    const response = await listWorldBooks({ query: ' 余烬 ', limit: 20, offset: 0 })

    expect(response.total).toBe(1)
    expect(response.world_books[0]?.name).toBe('余烬哨站设定')
    const url = String((mock.mock.calls[0] as unknown[])[0])
    expect(url).toBe('/api/v1/world-books?query=%E4%BD%99%E7%83%AC&limit=20&offset=0')
  })

  it('listWorldBooks 字段漂移 → 契约错误（不裸 as T）', async () => {
    mockFetch(() => jsonResponse(200, { world_books: [{ world_book_id: 'wb_x' }], total: 1 }))

    const error = (await listWorldBooks().catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.message).toContain('契约')
  })

  it('getWorldBook：解析 {world_book, entries} 并逐项校验条目字段', async () => {
    mockFetch(() =>
      jsonResponse(200, { world_book: SUMMARY, entries: [ENTRY] }),
    )

    const detail = await getWorldBook('wb_abc')

    expect(detail.entries).toHaveLength(1)
    expect(detail.entries[0]?.keys).toEqual(['哨站'])
  })

  it('importWorldBook：multipart + 幂等键，解析 201（含 warnings）', async () => {
    const mock = mockFetch(() =>
      jsonResponse(201, {
        world_book_id: 'wb_new',
        name: '导入的世界书',
        entry_count: 2,
        source: 'tavern',
        warnings: [{ code: 'UNKNOWN_FIELD', message: '已忽略未知字段 entry.position' }],
      }),
    )

    const response = await importWorldBook('{"entries":{}}', 'wb-import-key', {
      filename: 'book.json',
      name: '自定义名',
    })

    expect(response.world_book_id).toBe('wb_new')
    expect(response.warnings?.[0]?.code).toBe('UNKNOWN_FIELD')
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/world-books/import')
    expect(call[1]?.method).toBe('POST')
    expect(call[1]?.body).toBeInstanceOf(FormData)
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(headers.get('Content-Type')).toBeNull() // 浏览器自动带 multipart boundary
    const form = call[1]?.body as FormData
    expect(form.get('name')).toBe('自定义名')
    expect((form.get('file') as File).name).toBe('book.json')
  })

  it('validateWorldBook：POST {payload} 并解析 valid/errors/warnings/preview', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, {
        valid: false,
        errors: [{ loc: ['entries', 0, 'keys'], type: 'missing', msg: 'Field required' }],
        warnings: [],
        preview: { name: 'x', entry_count: 0, detected_format: 'unknown' },
      }),
    )

    const response = await validateWorldBook({ entries: [] })

    expect(response.valid).toBe(false)
    expect(response.errors?.[0]?.loc).toEqual(['entries', 0, 'keys'])
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/world-books/validate')
    expect(JSON.parse(String(call[1]?.body))).toEqual({ payload: { entries: [] } })
  })

  it('deleteWorldBook：DELETE + 幂等键，解析 {world_book_id, deleted}', async () => {
    const mock = mockFetch(() => jsonResponse(200, { world_book_id: 'wb_abc', deleted: true }))

    const response = await deleteWorldBook('wb_abc', 'wb-delete-key')

    expect(response.deleted).toBe(true)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/world-books/wb_abc')
    expect(call[1]?.method).toBe('DELETE')
    expect(new Headers(call[1]?.headers).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
  })
})
