import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import CardLibraryView from '@/views/CardLibraryView.vue'

const SUMMARY = {
  card_id: 'card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK',
  name: '灰烬旅人',
  source: 'upload',
  generated_by: null,
  updated_at: '2026-11-10T13:00:00Z',
}

/** 玩家可见文本允许出现的内部 ID 形式（AC-23：不得出现 card_ 前缀内部 ID）。 */
const INTERNAL_CARD_ID = /card_[0-9A-Z]{8,}/

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

  it('列表项只渲染玩家可读信息，不露内部 card_ ID（AC-23 脱敏）', async () => {
    const mock = vi.fn(async () => jsonResponse(200, { cards: [SUMMARY], total: 1 }))
    vi.stubGlobal('fetch', mock)
    const router = makeRouter()
    await router.push('/cards')
    await router.isReady()

    const wrapper = mount(CardLibraryView, { global: { plugins: [router] } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('灰烬旅人')
    expect(text).toContain('上传')
    expect(text).toContain('更新：2026-11-10 13:00:00 UTC')
    expect(text).not.toContain('ID：')
    expect(text).not.toMatch(INTERNAL_CARD_ID)
    // 链接 href 仍可用内部 ID（不可见文本，不违反 AC-23）
    expect(wrapper.find('a.card-name').attributes('href')).toBe(`/cards/${SUMMARY.card_id}`)
  })
})
