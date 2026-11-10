import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import WorldLorePanel from '@/components/WorldLorePanel.vue'
import WorldBookDetailView from '@/views/WorldBookDetailView.vue'
import WorldBookImportView from '@/views/WorldBookImportView.vue'
import WorldBookLibraryView from '@/views/WorldBookLibraryView.vue'

const SUMMARY = {
  world_book_id: 'wb_abc',
  name: '余烬哨站设定',
  description: '哨站与信标。',
  source: 'tavern',
  entry_count: 2,
  updated_at: '2026-11-11T01:00:00Z',
}

const ENTRY = {
  entry_uid: 1,
  keys: ['哨站', '信标'],
  secondary_keys: ['守夜人'],
  content: '余烬哨站是河谷里的旧哨站，信标已经灭了两夜。',
  enabled: true,
  constant: true,
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

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
      { path: '/world-books', name: 'world-books', component: WorldBookLibraryView },
      { path: '/world-books/import', name: 'world-book-import', component: WorldBookImportView },
      {
        path: '/world-books/:worldBookId',
        name: 'world-book-detail',
        component: WorldBookDetailView,
      },
    ],
  })
}

async function mountAt(component: unknown, path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component as never, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('世界书页面（H4）', () => {
  it('列表页：渲染名称/来源/条目数，空态有导入入口', async () => {
    const mock = vi.fn(async () => jsonResponse(200, { world_books: [SUMMARY], total: 1 }))
    vi.stubGlobal('fetch', mock)
    const { wrapper } = await mountAt(WorldBookLibraryView, '/world-books')

    const text = wrapper.text()
    expect(text).toContain('余烬哨站设定')
    expect(text).toContain('酒馆格式')
    expect(text).toContain('2 条设定')
    expect(String((mock.mock.calls[0] as unknown[] | undefined)?.[0])).toContain('/api/v1/world-books')
  })

  it('列表页空态：提示导入一本', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { world_books: [], total: 0 })))
    const { wrapper } = await mountAt(WorldBookLibraryView, '/world-books')
    expect(wrapper.text()).toContain('还没有世界书')
    expect(wrapper.text()).toContain('导入一本')
  })

  it('导入页：粘贴 JSON 校验通过后展示识别格式；本地 JSON 错误不发请求', async () => {
    const mock = vi.fn(async () =>
      jsonResponse(200, {
        valid: true,
        errors: [],
        warnings: [{ code: 'IGNORED', message: '已忽略未知字段' }],
        preview: { name: '余烬哨站设定', entry_count: 2, detected_format: 'tavern' },
      }),
    )
    vi.stubGlobal('fetch', mock)
    const { wrapper } = await mountAt(WorldBookImportView, '/world-books/import')

    await wrapper.find('textarea').setValue('{broken')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('JSON 语法错误')
    expect(mock).not.toHaveBeenCalled()

    await wrapper.find('textarea').setValue('{"entries":{}}')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('格式识别通过')
    expect(wrapper.text()).toContain('酒馆格式')
    expect(wrapper.text()).toContain('已忽略未知字段')
  })

  it('详情页：渲染条目（触发词/常驻/内容），删除需二次确认', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        calls.push(`${init?.method ?? 'GET'} ${url}`)
        if (url.includes('/world-books/wb_abc') && (init?.method ?? 'GET') === 'GET') {
          return jsonResponse(200, { world_book: { ...SUMMARY, source: 'tavern' }, entries: [ENTRY] })
        }
        if ((init?.method ?? 'GET') === 'DELETE') {
          return jsonResponse(200, { world_book_id: 'wb_abc', deleted: true })
        }
        return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
      }),
    )
    const { wrapper, router } = await mountAt(WorldBookDetailView, '/world-books/wb_abc')

    expect(wrapper.text()).toContain('余烬哨站设定')
    expect(wrapper.text()).toContain('哨站')
    expect(wrapper.text()).toContain('常驻')
    expect(wrapper.text()).toContain('信标已经灭了两夜')

    const deleteButton = wrapper.findAll('button').find((button) => button.text() === '删除')
    await deleteButton?.trigger('click')
    expect(wrapper.text()).toContain('确认删除')
    const confirm = wrapper.findAll('button').find((button) => button.text().includes('确认删除'))
    await confirm?.trigger('click')
    await flushPromises()

    expect(calls.some((call) => call.startsWith('DELETE '))).toBe(true)
    expect(router.currentRoute.value.name).toBe('world-books')
  })
})

describe('对局内「世界设定」面板', () => {
  it('无世界书不渲染；有激活条目时逐条显示内容', () => {
    const empty = mount(WorldLorePanel, {
      props: { worldBook: null, lore: null },
    })
    expect(empty.find('.world-lore-panel').exists()).toBe(false)

    const wrapper = mount(WorldLorePanel, {
      props: {
        worldBook: { world_book_id: 'wb_abc', name: '余烬哨站设定', entry_count: 2 },
        lore: { active_entries: [{ entry_uid: 1, content: '余烬哨站是河谷里的旧哨站。' }] },
      },
    })
    expect(wrapper.text()).toContain('世界设定')
    expect(wrapper.text()).toContain('余烬哨站设定')
    expect(wrapper.text()).toContain('余烬哨站是河谷里的旧哨站。')
  })
})
