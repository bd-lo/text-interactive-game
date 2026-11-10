import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useWorldBooksStore } from '@/stores/worldBooks'

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

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('世界书 store（列表/详情/校验/导入/软删）', () => {
  it('loadList：解析列表与总数；空列表不报错', async () => {
    mockFetch(() => jsonResponse(200, { world_books: [SUMMARY], total: 1 }))
    const store = useWorldBooksStore()

    await store.loadList()

    expect(store.items).toHaveLength(1)
    expect(store.total).toBe(1)
    expect(store.error).toBeNull()
  })

  it('搜索重置到第一页；失败写入玩家可读错误（手动重试）', async () => {
    const urls: string[] = []
    mockFetch((url) => {
      urls.push(url)
      if (url.includes('offset=0') && url.includes('query=')) return jsonResponse(200, { world_books: [], total: 0 })
      return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '没有' })
    })
    const store = useWorldBooksStore()
    store.offset = 40

    await store.search('余烬')

    expect(urls[0]).toContain('offset=0')
    expect(store.items).toEqual([])

    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'db down' }))
    await store.loadList().catch(() => undefined)
    expect(store.error?.title).toBeTruthy()
  })

  it('loadDetail：成功填充摘要+条目；404 转玩家可读错误', async () => {
    mockFetch(() => jsonResponse(200, { world_book: SUMMARY, entries: [ENTRY] }))
    const store = useWorldBooksStore()

    await store.loadDetail('wb_abc')

    expect(store.detail?.name).toBe('余烬哨站设定')
    expect(store.entries[0]?.content).toContain('哨站')

    mockFetch(() =>
      jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '世界书不存在: wb_x' }),
    )
    await store.loadDetail('wb_x').catch(() => undefined)
    expect(store.detailError?.title).toContain('找不到')
  })

  it('validate：本地预检失败不发请求；服务端结果（preview/errors）入 store', async () => {
    const mock = mockFetch(() => jsonResponse(200, { valid: true, errors: [], warnings: [], preview: { name: 'x', entry_count: 2, detected_format: 'tavern' } }))
    const store = useWorldBooksStore()

    await store.validate('{broken')
    expect(store.validateError).toContain('JSON 语法错误')
    expect(mock).not.toHaveBeenCalled()

    await store.validate('{"entries":[]}')
    expect(mock).toHaveBeenCalledTimes(1)
    expect(store.validateResult?.valid).toBe(true)
    expect(store.validateResult?.preview?.detected_format).toBe('tavern')
  })

  it('importText：预检拦截；成功后返回 world_book_id 与 warnings', async () => {
    const mock = mockFetch(() => jsonResponse(201, {
      world_book_id: 'wb_new',
      name: '导入',
      entry_count: 2,
      source: 'tavern',
      warnings: [{ code: 'IGNORED', message: '已忽略未知字段' }],
    }))
    const store = useWorldBooksStore()

    expect(await store.importText('')).toBeNull()
    expect(mock).not.toHaveBeenCalled()

    const id = await store.importText('{"entries":{}}')
    expect(id).toBe('wb_new')
    expect(store.importWarnings[0]?.message).toContain('已忽略')
    expect(store.pendingImportKey).toBeNull()
  })

  it('importText 409：清键后下一次提交自动换新键（无自动重试）', async () => {
    const keys: Array<string | null> = []
    let calls = 0
    mockFetch((_url, init) => {
      calls += 1
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      if (calls === 1) return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '同键异参' })
      return jsonResponse(201, {
        world_book_id: 'wb_new',
        name: '导入',
        entry_count: 1,
        source: 'manual',
        warnings: [],
      })
    })
    const store = useWorldBooksStore()

    expect(await store.importText('{"a":1}')).toBeNull()
    expect(await store.importText('{"a":2}')).toBe('wb_new')
    expect(keys[1]).not.toBe(keys[0])
  })

  it('remove：软删成功从列表移除并减总数；失败保留并给可读错误', async () => {
    mockFetch(() => jsonResponse(200, { world_books: [SUMMARY], total: 1 }))
    const store = useWorldBooksStore()
    await store.loadList()

    mockFetch(() => jsonResponse(200, { world_book_id: 'wb_abc', deleted: true }))
    expect(await store.remove('wb_abc')).toBe(true)
    expect(store.items).toHaveLength(0)
    expect(store.total).toBe(0)

    mockFetch(() => jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '世界书不存在' }))
    expect(await store.remove('wb_abc')).toBe(false)
    expect(store.deleteError?.title).toBeTruthy()
  })
})
