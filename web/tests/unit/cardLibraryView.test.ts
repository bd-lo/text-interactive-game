import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import CardLibraryView from '@/views/CardLibraryView.vue'

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
      { path: '/cards', name: 'cards', component: CardLibraryView },
      { path: '/cards/import', name: 'card-import', component: { template: '<div>import</div>' } },
      { path: '/cards/generate', name: 'card-generate', component: { template: '<div>generate</div>' } },
      { path: '/cards/:cardId', name: 'card-detail', component: { template: '<div>detail</div>' } },
    ],
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('CardLibraryView（卡库页空态 + 导入入口）', () => {
  it('列表为空时显示空态与导入入口，且发起 GET /character-cards', async () => {
    const mock = vi.fn(async () => jsonResponse(200, { cards: [], total: 0 }))
    vi.stubGlobal('fetch', mock)
    const router = makeRouter()
    await router.push('/cards')
    await router.isReady()

    const wrapper = mount(CardLibraryView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('暂无人物卡')
    expect(wrapper.text()).toContain('导入卡片')
    expect(wrapper.text()).toContain('共 0 张')
    expect(mock).toHaveBeenCalledTimes(1)
    expect(String((mock.mock.calls[0] as unknown[])[0])).toContain('/api/v1/character-cards')
  })
})
