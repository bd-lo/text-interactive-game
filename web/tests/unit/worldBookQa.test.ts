/**
 * H4 QA：世界书前端独立回归（utils 预检 + Pinia store 行为 + 契约漂移防护）。
 *
 * 与 frontend-dev 自己的测试互补：这里只写 `worldBook*.test.ts` 新文件，用 **stub fetch +
 * 真实 api/client 解析**（不 mock 掉 client），因此能同时钉住「store 行为」与
 * 「H4-CONTRACT §2/§7 的响应字段契约」；覆盖导入幂等键、409 清键、413 人话、软删与分页。
 */

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClientError } from '@/api/client'
import { useWorldBooksStore } from '@/stores/worldBooks'
import {
  WORLD_BOOK_MAX_BYTES,
  checkWorldBookFileSize,
  checkWorldBookText,
  describeWorldBookError,
  labelDetectedFormat,
  labelSelectiveLogic,
  labelWorldBookSource,
} from '@/utils/worldBooks'

const BOOK_ID = 'wb_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK'
const UPDATED = '2026-11-11T04:00:00Z'

const SUMMARY = {
  world_book_id: BOOK_ID,
  name: '余烬哨站设定集',
  description: '给 story 补充世界观。',
  source: 'tavern',
  entry_count: 2,
  updated_at: UPDATED,
}

const ENTRY = {
  entry_uid: 1,
  keys: ['断桥', '雪'],
  secondary_keys: [],
  content: '断桥之下是封冻的河面。',
  enabled: true,
  constant: false,
  order: 10,
  position: 0,
  depth: 4,
  selective: true,
  selective_logic: 0,
}

const VALIDATE_BODY = {
  valid: true,
  errors: [],
  warnings: [],
  preview: { name: '余烬哨站设定集', entry_count: 2, detected_format: 'tavern' },
}

const IMPORT_BODY = {
  world_book_id: BOOK_ID,
  name: '余烬哨站设定集',
  entry_count: 2,
  source: 'tavern',
  warnings: [{ code: 'FIELD_IGNORED', message: '已忽略未实现字段 probability 之外的字段' }],
}

const LIBRARY_TEXT = JSON.stringify({
  name: '余烬哨站设定集',
  entries: [
    { uid: 1, keys: ['断桥'], content: '断桥之下是封冻的河面。' },
    { uid: 2, keys: ['老兵'], content: '守夜人老兵守着哨站。' },
  ],
})

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

function defaultHandler(url: string, init?: RequestInit): Response {
  const method = (init?.method ?? 'GET').toUpperCase()
  if (url.includes('/api/v1/world-books/validate')) {
    return jsonResponse(200, VALIDATE_BODY)
  }
  if (url.includes('/api/v1/world-books/import')) {
    return jsonResponse(201, IMPORT_BODY)
  }
  if (method === 'DELETE') {
    return jsonResponse(200, { world_book_id: BOOK_ID, deleted: true })
  }
  if (/\/api\/v1\/world-books\/[^?]+/.test(url)) {
    return jsonResponse(200, { world_book: SUMMARY, entries: [ENTRY] })
  }
  if (url.includes('/api/v1/world-books')) {
    return jsonResponse(200, { world_books: [SUMMARY], total: 3 })
  }
  throw new Error(`未预期的请求：${method} ${url}`)
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

describe('世界书 utils（客户端预检/人话/标签）', () => {
  it('文件大小边界：≤256KB 通过，超 1 字节即拒绝', () => {
    expect(checkWorldBookFileSize(0)).toBe('')
    expect(checkWorldBookFileSize(WORLD_BOOK_MAX_BYTES)).toBe('')
    const tooBig = checkWorldBookFileSize(WORLD_BOOK_MAX_BYTES + 1)
    expect(tooBig).toContain('256KB')
  })

  it('文本预检：空/语法错误/根非对象/超限/合法', () => {
    expect(checkWorldBookText('').ok).toBe(false)
    expect(checkWorldBookText('   ').error).toContain('内容为空')
    expect(checkWorldBookText('{bad json').error).toContain('JSON 语法错误')
    expect(checkWorldBookText('[1,2,3]').error).toContain('根节点必须是对象')
    const oversized = `{"pad":"${'x'.repeat(WORLD_BOOK_MAX_BYTES)}"}`
    expect(checkWorldBookText(oversized).error).toContain('256KB')
    const ok = checkWorldBookText(LIBRARY_TEXT)
    expect(ok.ok).toBe(true)
    expect(ok.bytes).toBe(new TextEncoder().encode(LIBRARY_TEXT).length)
  })

  it('来源/识别格式/selectiveLogic 中文标签', () => {
    expect(labelWorldBookSource('tavern')).toBe('酒馆格式')
    expect(labelWorldBookSource('character_card')).toBe('角色卡内嵌')
    expect(labelWorldBookSource('manual')).toBe('简化格式')
    expect(labelWorldBookSource('future_source')).toBe('future_source')
    expect(labelDetectedFormat(undefined)).toBe('未知格式')
    expect(labelDetectedFormat('tavern')).toBe('酒馆格式')
    expect(labelSelectiveLogic(0)).toContain('任一命中')
    expect(labelSelectiveLogic(1)).toContain('NOT_ALL')
    expect(labelSelectiveLogic(2)).toContain('NOT_ANY')
    expect(labelSelectiveLogic(3)).toContain('AND_ALL')
  })

  it('413 错误人话：提示 256KB 上限（不透出后端原始报错）', () => {
    const info = describeWorldBookError(new ApiClientError(413, 'INVALID_INPUT', 'payload too large'))
    expect(info.title).toBe('文件太大了')
    expect(info.message).toContain('256KB')
  })
})

describe('世界书 store（列表/分页/搜索）', () => {
  it('loadList 成功：items/total 写入，分页 getter 正确', async () => {
    mockFetch(defaultHandler)
    const store = useWorldBooksStore()

    await store.loadList()

    expect(store.items).toHaveLength(1)
    expect(store.total).toBe(3)
    expect(store.page).toBe(1)
    expect(store.totalPages).toBe(1)
    expect(store.canPrev).toBe(false)
    expect(store.error).toBeNull()
  })

  it('search 重置 offset=0 并传 query；服务端错误转为玩家可读 error 且 rethrow', async () => {
    const calls: string[] = []
    const mock = mockFetch((url, init) => {
      void init
      calls.push(url)
      return jsonResponse(200, { world_books: [], total: 0 })
    })
    const store = useWorldBooksStore()
    await store.loadList({ offset: 40 })
    await store.search('哨站')
    expect(store.offset).toBe(0)
    expect(store.query).toBe('哨站')
    expect(calls[calls.length - 1]).toContain('query=%E5%93%A8%E7%AB%99')

    mock.mockImplementation(async () => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'x' }))
    await expect(store.loadList()).rejects.toBeTruthy()
    expect(store.items).toEqual([])
    expect(store.error).not.toBeNull()
  })
})

describe('世界书 store（详情/校验/导入/删除）', () => {
  it('loadDetail 成功写入 detail+entries；404 转为 detailError', async () => {
    mockFetch(defaultHandler)
    const store = useWorldBooksStore()
    await store.loadDetail(BOOK_ID)
    expect(store.detail?.name).toBe('余烬哨站设定集')
    expect(store.entries).toHaveLength(1)
    expect(store.detailLoaded).toBe(true)

    mockFetch(() => jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '世界书不存在' }))
    const failed = useWorldBooksStore()
    await expect(failed.loadDetail('wb_missing')).rejects.toBeTruthy()
    expect(failed.detailError).not.toBeNull()
    expect(failed.detailLoaded).toBe(false)
  })

  it('validate：客户端预检失败不发请求；服务端结果原样保留', async () => {
    const mock = mockFetch(defaultHandler)
    const store = useWorldBooksStore()
    await store.validate('')
    expect(store.validateError).toContain('内容为空')
    expect(mock).not.toHaveBeenCalled()

    await store.validate(LIBRARY_TEXT)
    expect(store.validateResult?.valid).toBe(true)
    expect(store.validateResult?.preview?.detected_format).toBe('tavern')
  })

  it('importText：预检失败不发请求；成功返回 id 并保留 warnings', async () => {
    const mock = mockFetch(defaultHandler)
    const store = useWorldBooksStore()

    const blocked = await store.importText('not-json')
    expect(blocked).toBeNull()
    expect(store.importError?.message).toContain('JSON 语法错误')
    expect(mock).not.toHaveBeenCalled()

    const bookId = await store.importText(LIBRARY_TEXT, { filename: 'book.json' })
    expect(bookId).toBe(BOOK_ID)
    expect(store.importResult?.entry_count).toBe(2)
    expect(store.importWarnings).toHaveLength(1)

    const [url, init] = mock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/world-books/import')
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy()
  })

  it('importText 409：清空 pendingImportKey（下次换新键），不自动重试', async () => {
    const mock = mockFetch(() =>
      jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '该 Idempotency-Key 已用于不同参数' }),
    )
    const store = useWorldBooksStore()

    const first = await store.importText(LIBRARY_TEXT)
    expect(first).toBeNull()
    expect(store.pendingImportKey).toBeNull()
    expect(store.importError).not.toBeNull()

    const second = await store.importText(LIBRARY_TEXT)
    expect(second).toBeNull()
    expect(mock).toHaveBeenCalledTimes(2) // 每次都是用户手动触发，无自动重试
  })

  it('remove 成功：软删后从列表移除并减 total；失败设置 deleteError 且列表不动', async () => {
    mockFetch(defaultHandler)
    const store = useWorldBooksStore()
    await store.loadList()
    expect(store.items).toHaveLength(1)

    const removed = await store.remove(BOOK_ID)
    expect(removed).toBe(true)
    expect(store.items).toHaveLength(0)
    expect(store.total).toBe(2)

    mockFetch(() => jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: '世界书不存在' }))
    const failed = useWorldBooksStore()
    failed.items = [SUMMARY]
    failed.total = 1
    const failedRemove = await failed.remove('wb_missing')
    expect(failedRemove).toBe(false)
    expect(failed.deleteError).not.toBeNull()
    expect(failed.items).toHaveLength(1)
  })

  it('删除二次确认状态：armDelete/disarmDelete 可切换', () => {
    const store = useWorldBooksStore()
    expect(store.deleteArmed).toBe(false)
    store.armDelete()
    expect(store.deleteArmed).toBe(true)
    store.disarmDelete()
    expect(store.deleteArmed).toBe(false)
  })
})
