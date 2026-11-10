import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import GenerateCardView from '@/views/GenerateCardView.vue'
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

function templateResponse() {
  return {
    draft: CARD,
    validation: {
      valid: true,
      errors: [],
      warnings: [{ code: 'TEMPLATE_FALLBACK', message: 'LLM 不可用；已使用确定性模板草稿', field: null }],
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

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/cards', name: 'cards', component: { template: '<div>cards</div>' } },
      { path: '/cards/import', name: 'card-import', component: { template: '<div>import</div>' } },
      { path: '/cards/generate', name: 'card-generate', component: GenerateCardView },
      { path: '/cards/:cardId', name: 'card-detail', component: { template: '<div>detail</div>' } },
      { path: '/game/:id', name: 'game', component: { template: '<div>game</div>' } },
    ],
  })
}

async function mountView(handler: Handler) {
  const mock = mockFetch(handler)
  const router = makeRouter()
  await router.push('/cards/generate')
  await router.isReady()
  const wrapper = mount(GenerateCardView, { global: { plugins: [router] } })
  return { wrapper, mock, router }
}

/** 生成 + 保存两步所需的通用后端替身（03 端点）。 */
function fullHandler(overrides: { generate?: unknown } = {}): Handler {
  return (url) => {
    if (url === '/api/v1/character-cards/generate') {
      return jsonResponse(200, overrides.generate ?? templateResponse())
    }
    if (url === '/api/v1/character-cards/validate') {
      return jsonResponse(200, { valid: true, errors: [] })
    }
    if (url === '/api/v1/character-cards') {
      return jsonResponse(201, SAVED)
    }
    if (url === '/api/v1/games') {
      return jsonResponse(201, CREATED)
    }
    if (url === '/api/v1/games/g1/view') {
      return jsonResponse(200, PLAYER_VIEW)
    }
    return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
  }
}

async function generateOnce(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('[data-testid=brief-input]').setValue('旧矿镇的拾荒者')
  await wrapper.find('form.generate-form').trigger('submit')
  await flushPromises()
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

describe('GenerateCardView（M6-028 生成人物卡页面）', () => {
  it('页面根/离线提示/初始禁用状态：显著说明「默认离线=模板草稿」', async () => {
    const { wrapper } = await mountView(() => jsonResponse(500, {}))

    expect(wrapper.find('[data-testid=generate-card-page]').exists()).toBe(true)
    const hint = wrapper.find('[data-testid=offline-hint]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('离线')
    expect(hint.text()).toContain('模板草稿')
    expect(hint.text()).toContain('配置 LLM 后才是 LLM 生成')
    expect(wrapper.find('[data-testid=brief-counter]').text()).toBe('0/1000')
    expect(wrapper.find('[data-testid=generate-button]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid=draft-preview]').exists()).toBe(false)
  })

  it('brief 码点计数：emoji 计 1；超 1000 时生成按钮禁用并提示', async () => {
    const { wrapper } = await mountView(() => jsonResponse(500, {}))

    await wrapper.find('[data-testid=brief-input]').setValue('🎲')
    expect(wrapper.find('[data-testid=brief-counter]').text()).toBe('1/1000')
    expect(wrapper.find('[data-testid=generate-button]').attributes('disabled')).toBeUndefined()

    await wrapper.find('[data-testid=brief-input]').setValue('🎲'.repeat(1001))
    expect(wrapper.find('[data-testid=brief-counter]').text()).toBe('1001/1000')
    expect(wrapper.find('[data-testid=brief-counter]').classes()).toContain('counter-over')
    expect(wrapper.find('[data-testid=generate-button]').attributes('disabled')).toBeDefined()
  })

  it('生成（离线模板兜底）：草稿预览 9 字段 + 「模板草稿」徽章 + 提示列表', async () => {
    const { wrapper, mock } = await mountView(fullHandler())

    await generateOnce(wrapper)

    expect(mock).toHaveBeenCalledTimes(1)
    expect(String((mock.mock.calls[0] as [unknown, unknown])[0])).toBe('/api/v1/character-cards/generate')
    const preview = wrapper.find('[data-testid=draft-preview]')
    expect(preview.exists()).toBe(true)
    expect(preview.text()).toContain('灰烬旅人')
    expect(preview.text()).toContain('旧矿镇')
    expect(wrapper.find('[data-testid=draft-badge]').text()).toBe('模板草稿')
    expect(preview.text()).toContain('未调用 LLM')
  })

  it('生成（LLM）：徽章显示「LLM 草稿」', async () => {
    const { wrapper } = await mountView(
      fullHandler({ generate: llmResponse() }),
    )

    await generateOnce(wrapper)

    expect(wrapper.find('[data-testid=draft-badge]').text()).toBe('LLM 草稿')
  })

  it('编辑 JSON 后保存禁用；本地 JSON 错误不发 validate；重新校验通过后可保存并出现成功区', async () => {
    const { wrapper, mock } = await mountView(fullHandler())
    await generateOnce(wrapper)

    const saveButton = () => wrapper.find('[data-testid=save-card-button]')
    expect(saveButton().attributes('disabled')).toBeUndefined()

    await wrapper.find('[data-testid=draft-json]').setValue('{oops')
    expect(saveButton().attributes('disabled')).toBeDefined()

    await wrapper.find('[data-testid=revalidate-button]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('JSON 语法错误')
    expect(mock.mock.calls.some((call) => String(call[0]) === '/api/v1/character-cards/validate')).toBe(
      false,
    )
    expect(saveButton().attributes('disabled')).toBeDefined()

    await wrapper.find('[data-testid=draft-json]').setValue(JSON.stringify({ ...CARD, name: '新名字旅人' }))
    await wrapper.find('[data-testid=revalidate-button]').trigger('click')
    await flushPromises()
    expect(saveButton().attributes('disabled')).toBeUndefined()

    await saveButton().trigger('click')
    await flushPromises()

    const result = wrapper.find('[data-testid=save-result]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('已保存到卡库')
    // P1-9：玩家可见文本不露内部 card_ ID（ID 只存在于链接 href/状态，不进入主文案）
    expect(result.text()).not.toContain(SAVED.card_id)
    expect(wrapper.text()).not.toContain('人物卡 ID')
    expect(wrapper.text()).not.toMatch(/card_[0-9A-Z]{8,}/)
    expect(wrapper.find('[data-testid=open-card]').attributes('href')).toBe(
      `/cards/${SAVED.card_id}`,
    )
    expect(wrapper.find('[data-testid=start-game-with-card]').exists()).toBe(true)
    // 保存前绝不自动建局/跳转
    expect(mock.mock.calls.some((call) => String(call[0]) === '/api/v1/games')).toBe(false)
  })

  it('保存成功后点「按此卡开局」：POST /games {card_id} 并跳转 /game/:id', async () => {
    const { wrapper, mock, router } = await mountView(fullHandler())
    await generateOnce(wrapper)
    await wrapper.find('[data-testid=save-card-button]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid=start-game-with-card]').trigger('click')
    await flushPromises()

    const gameCall = mock.mock.calls.find((call) => String(call[0]) === '/api/v1/games')
    expect(gameCall).toBeDefined()
    expect(JSON.parse(String((gameCall?.[1] as RequestInit | undefined)?.body))).toEqual({
      card_id: SAVED.card_id,
    })
    expect(router.currentRoute.value.name).toBe('game')
    expect(router.currentRoute.value.params.id).toBe('g1')
  })

  it('生成 429：生成失败区显示「请 N 秒后重试」（Retry-After），不自动重试', async () => {
    const { wrapper, mock } = await mountView((url) => {
      if (url === '/api/v1/character-cards/generate') {
        return jsonResponse(
          429,
          { ok: false, error_code: 'OUT_OF_RANGE', message: '人物卡生成请求过于频繁' },
          { 'Retry-After': '25' },
        )
      }
      return jsonResponse(500, {})
    })

    await generateOnce(wrapper)

    const errorBox = wrapper.find('[data-testid=generate-error]')
    expect(errorBox.exists()).toBe(true)
    expect(errorBox.text()).toContain('25 秒后重试')
    expect(errorBox.text()).toContain('生成太频繁')
    expect(wrapper.find('[data-testid=draft-preview]').exists()).toBe(false)
    expect(mock).toHaveBeenCalledTimes(1)
  })

  it('卡库顶部入口：卡库页含「生成人物卡」链接且指向 /cards/generate', async () => {
    // 入口在 CardLibraryView；单独挂载，避免与生成页逻辑耦合。
    const { default: CardLibraryView } = await import('@/views/CardLibraryView.vue')
    const router = makeRouter()
    await router.push('/cards')
    await router.isReady()
    mockFetch(() => jsonResponse(200, { cards: [], total: 0 }))

    const wrapper = mount(CardLibraryView, { global: { plugins: [router] } })
    await flushPromises()

    const link = wrapper.find('a[href="/cards/generate"]')
    expect(link.exists()).toBe(true)
    expect(link.text()).toContain('生成人物卡')
  })
})
