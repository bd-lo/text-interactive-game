import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { CharacterCard } from '@/api/types'
import CardDetailView from '@/views/CardDetailView.vue'

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

const CARD_ID = 'card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK'
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

/** 玩家可见文本不得出现的内部 card ID 形式（AC-23 脱敏）。 */
const INTERNAL_CARD_ID = /card_[0-9A-Z]{8,}/

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/cards', name: 'cards', component: { template: '<div>cards</div>' } },
      { path: '/cards/:cardId', name: 'card-detail', component: CardDetailView },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
    ],
  })
}

async function mountDetail(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  const router = makeRouter()
  await router.push(`/cards/${CARD_ID}`)
  await router.isReady()
  const wrapper = mount(CardDetailView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, mock, router }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('CardDetailView（人物卡详情：9 字段 + 内部 ID 脱敏）', () => {
  it('正常详情：渲染 9 字段，标题与可见文本不露内部 card_ ID', async () => {
    const { wrapper, mock } = await mountDetail(() => jsonResponse(200, { card: CARD }))

    const text = wrapper.text()
    expect(text).toContain('人物卡详情')
    expect(text).toContain('灰烬旅人')
    expect(text).toContain('30 / 30')
    expect(text).toContain('火把') // 初始物品模板键中文化
    expect(text).toContain('schema 版本')
    expect(text).not.toContain(CARD_ID)
    expect(text).not.toMatch(INTERNAL_CARD_ID)

    expect(String((mock.mock.calls[0] as unknown[])[0])).toContain(
      `/api/v1/character-cards/${CARD_ID}`,
    )
  })

  it('详情 404：错误文案玩家可读且不含内部 card_ ID（服务端 message 带 ID 也不外露）', async () => {
    const { wrapper } = await mountDetail(() =>
      jsonResponse(404, {
        ok: false,
        error_code: 'NOT_FOUND',
        message: `人物卡不存在: ${CARD_ID}`,
      }),
    )

    const text = wrapper.text()
    expect(text).toContain('人物卡不存在')
    expect(text).toContain('404')
    expect(text).toContain('可能已被删除')
    expect(text).not.toContain(CARD_ID)
    expect(text).not.toMatch(INTERNAL_CARD_ID)
  })

  it('详情页「开始游戏」仍按卡开局（脱敏不改内部调用链）', async () => {
    const { wrapper, mock, router } = await mountDetail((url) => {
      if (url === `/api/v1/character-cards/${CARD_ID}`) {
        return jsonResponse(200, { card: CARD })
      }
      if (url === '/api/v1/games') {
        return jsonResponse(201, CREATED)
      }
      if (url === '/api/v1/games/g1/view') {
        return jsonResponse(200, PLAYER_VIEW)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    const startButton = wrapper.findAll('button').find((button) => button.text().includes('开始游戏'))
    expect(startButton).toBeDefined()
    await startButton?.trigger('click')
    await flushPromises()

    const gameCall = mock.mock.calls.find((call) => String(call[0]) === '/api/v1/games')
    expect(gameCall).toBeDefined()
    expect(JSON.parse(String((gameCall?.[1] as RequestInit | undefined)?.body))).toEqual({
      card_id: CARD_ID,
    })
    expect(router.currentRoute.value.name).toBe('game')
  })
})
