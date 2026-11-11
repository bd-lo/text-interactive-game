import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  SCENARIO_LIST_DEFAULT_LIMIT,
  useScenariosStore,
} from '@/stores/scenarios'

const STATS = { rooms: 2, items: 1, npcs: 1, objectives: 1, endings: 1, suggestions: 0 }

function summary(id: string, title: string) {
  return {
    scenario_id: id,
    slug: id.replace('scn_', ''),
    title,
    author: '示例',
    description: '一段简介',
    schema_version: 1,
    created_at: '2026-11-11T10:00:00Z',
    summary: STATS,
    warnings: [],
  }
}

const FIRST = summary('scn_first', '小塔')
const SECOND = summary('scn_second', '余烬哨站')

const DEFINITION = {
  schema_version: 1,
  slug: 'tiny-tower',
  meta: { title: '小塔' },
  player: { location: 'gate' },
  rooms: [{ key: 'gate', name: '塔门', description: '一扇虚掩的木门。' }],
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

function unexpected(url: string): Response {
  return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('scenarios store（列表分页/搜索、详情、校验、导入、软删）', () => {
  it('列表加载与搜索：q/limit/offset 组合，搜索重置第一页', async () => {
    const urls: string[] = []
    mockFetch((url) => {
      urls.push(url)
      if (url.startsWith('/api/v1/scenarios?')) {
        return jsonResponse(200, { scenarios: [FIRST], total: 45 })
      }
      return unexpected(url)
    })
    const store = useScenariosStore()

    await store.loadList()
    expect(store.items).toHaveLength(1)
    expect(store.total).toBe(45)
    expect(store.canNext).toBe(true)
    expect(store.canPrev).toBe(false)
    expect(urls[0]).toContain(`limit=${SCENARIO_LIST_DEFAULT_LIMIT}`)
    expect(urls[0]).toContain('offset=0')

    await store.nextPage()
    expect(store.offset).toBe(SCENARIO_LIST_DEFAULT_LIMIT)
    expect(store.canPrev).toBe(true)

    await store.search('  余烬 ')
    expect(store.offset).toBe(0)
    expect(urls[urls.length - 1]).toContain('q=%E4%BD%99%E7%83%AC')
  })

  it('列表失败：错误转玩家可读并清空列表（由视图手动重试）', async () => {
    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' }))
    const store = useScenariosStore()

    await expect(store.loadList()).rejects.toBeTruthy()

    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
    expect(store.error?.title).toBe('出了点问题')
    expect(store.loading).toBe(false)
  })

  it('详情加载：成功写入 detail；404 → 玩家可读「找不到这个场景」', async () => {
    mockFetch((url) => {
      if (url === '/api/v1/scenarios/scn_first') {
        return jsonResponse(200, { ...FIRST, definition: DEFINITION })
      }
      if (url === '/api/v1/scenarios/scn_gone') {
        return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: 'not found' })
      }
      return unexpected(url)
    })
    const store = useScenariosStore()

    await store.loadDetail('scn_first')
    expect(store.detail?.title).toBe('小塔')
    expect(store.detail?.definition).toEqual(DEFINITION)

    await expect(store.loadDetail('scn_gone')).rejects.toBeTruthy()
    expect(store.detail).toBeNull()
    expect(store.detailError?.message).toContain('可能不存在')
  })

  it('校验：本地 JSON 预检失败不发请求；服务端 ok=true 后 canImport 才为 true', async () => {
    const mock = mockFetch(() =>
      jsonResponse(200, { ok: true, errors: [], warnings: [], summary: STATS }),
    )
    const store = useScenariosStore()

    await store.validate('{broken')
    expect(store.validateError).toContain('JSON 语法错误')
    expect(mock).not.toHaveBeenCalled()
    expect(store.canImport).toBe(false)

    await store.validate(JSON.stringify(DEFINITION))
    expect(store.validateResult?.ok).toBe(true)
    expect(store.canImport).toBe(true)

    store.resetValidate()
    expect(store.validateResult).toBeNull()
    expect(store.canImport).toBe(false)
  })

  it('导入成功：multipart + 幂等键，返回 scenario_id 并记录 warnings；成功后清幂等键', async () => {
    const mock = mockFetch(() =>
      jsonResponse(201, {
        scenario_id: 'scn_new',
        slug: 'tiny-tower',
        title: '小塔',
        summary: STATS,
        warnings: [{ path: 'items[0]', message: '未使用的物品' }],
      }),
    )
    const store = useScenariosStore()

    const scenarioId = await store.importText(JSON.stringify(DEFINITION), {
      filename: 'tiny-tower.json',
    })

    expect(scenarioId).toBe('scn_new')
    expect(store.importResult?.title).toBe('小塔')
    expect(store.importWarnings[0]?.path).toBe('items[0]')
    expect(store.importError).toBeNull()
    expect(store.pendingImportKey).toBeNull()
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(call[1]?.body).toBeInstanceOf(FormData)
  })

  it('导入失败：错误入 importError，返回 null，可手动重试', async () => {
    mockFetch(() => jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' }))
    const store = useScenariosStore()

    const scenarioId = await store.importText(JSON.stringify(DEFINITION))

    expect(scenarioId).toBeNull()
    expect(store.importError?.title).toBe('出了点问题')
    expect(store.importBusy).toBe(false)
  })

  it('导入 409 同键异参：清掉旧键，下次提交换新键（不是自动重试）', async () => {
    const keys: Array<string | null> = []
    let calls = 0
    mockFetch((_url, init) => {
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      calls += 1
      if (calls === 1) {
        return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: 'conflict' })
      }
      return jsonResponse(201, {
        scenario_id: 'scn_new',
        slug: 'tiny-tower',
        title: '小塔',
        summary: STATS,
        warnings: [],
      })
    })
    const store = useScenariosStore()

    expect(await store.importText(JSON.stringify(DEFINITION))).toBeNull()
    expect(store.pendingImportKey).toBeNull()

    expect(await store.importText(JSON.stringify(DEFINITION))).toBe('scn_new')
    expect(keys[0]).not.toBeNull()
    expect(keys[1]).not.toBeNull()
    expect(keys[0]).not.toBe(keys[1])
  })

  it('软删成功：从列表移除并减 total；失败保留列表并写 deleteError', async () => {
    mockFetch((url, init) => {
      if (url === '/api/v1/scenarios/scn_first' && init?.method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      if (url === '/api/v1/scenarios/scn_second' && init?.method === 'DELETE') {
        return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
      }
      return jsonResponse(200, { scenarios: [FIRST, SECOND], total: 2 })
    })
    const store = useScenariosStore()
    await store.loadList()

    expect(await store.remove('scn_first')).toBe(true)
    expect(store.items.map((item) => item.scenario_id)).toEqual(['scn_second'])
    expect(store.total).toBe(1)
    expect(sessionStorage.getItem('idem:scenario-delete:scn_first')).toBeNull()

    expect(await store.remove('scn_second')).toBe(false)
    expect(store.items).toHaveLength(1)
    expect(store.deleteError?.title).toBe('出了点问题')
  })

  it('删除二次确认由视图控制：arm/disarm；resetImport 清空导入态', () => {
    const store = useScenariosStore()
    store.armDelete()
    expect(store.deleteArmed).toBe(true)
    store.disarmDelete()
    expect(store.deleteArmed).toBe(false)

    store.importWarnings = [{ path: 'x', message: 'y' }]
    store.resetImport()
    expect(store.importWarnings).toEqual([])
    expect(store.importResult).toBeNull()
  })
})
