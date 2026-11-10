import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CharacterCard } from '@/api/types'
import {
  GENERATE_IDEMPOTENCY_ACTION,
  SAVE_IDEMPOTENCY_ACTION,
  useCardGenerateStore,
} from '@/stores/cardGenerate'

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
  setActivePinia(createPinia())
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('生成人物卡 store（M6-028）', () => {
  it('离线模板兜底：generated_by=null + TEMPLATE_FALLBACK → templateFallback=true，可保存', async () => {
    const mock = mockFetch(() => jsonResponse(200, templateResponse()))
    const store = useCardGenerateStore()
    store.brief = '  旧矿镇的拾荒者  '

    const ok = await store.generate()

    expect(ok).toBe(true)
    expect(store.busy).toBe(false)
    expect(store.draft?.name).toBe('灰烬旅人')
    expect(store.generatedBy).toBeNull()
    expect(store.templateFallback).toBe(true)
    expect(store.badgeLabel).toBe('模板草稿')
    expect(store.canSave).toBe(true)
    expect(JSON.parse(store.editedText)).toEqual(CARD)
    expect(bodyOf(mock, 0)).toEqual({
      brief: '旧矿镇的拾荒者',
      schema_version: '1.0',
      options: {},
    })
    expect(headersOf(mock, 0).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('LLM 生成：generated_by 非 null 且无兜底 warning → templateFallback=false', async () => {
    mockFetch(() => jsonResponse(200, llmResponse()))
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'
    store.tone = 'grim'

    expect(await store.generate()).toBe(true)
    expect(store.templateFallback).toBe(false)
    expect(store.badgeLabel).toBe('LLM 草稿')
    expect(store.generatedBy).toBe('deepseek:chat')
  })

  it('模板兜底容错：generated_by 非 null 但 warnings 含 TEMPLATE_FALLBACK 仍判兜底', async () => {
    mockFetch(() =>
      jsonResponse(200, {
        ...llmResponse(),
        validation: {
          valid: true,
          errors: [],
          warnings: [{ code: 'TEMPLATE_FALLBACK', message: '兜底' }],
        },
      }),
    )
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'

    await store.generate()

    expect(store.templateFallback).toBe(true)
    expect(store.badgeLabel).toBe('模板草稿')
  })

  it('brief 为空/超限：不发请求、给出中文预检错误', async () => {
    const mock = mockFetch(() => jsonResponse(200, templateResponse()))
    const store = useCardGenerateStore()

    expect(await store.generate()).toBe(false)
    expect(store.error).toContain('人物设定')
    expect(store.errorCode).toBe('CLIENT')

    store.brief = '🎲'.repeat(1001)
    expect(store.briefOverLimit).toBe(true)
    expect(await store.generate()).toBe(false)
    expect(store.error).toContain('不能超过 1000 字')

    expect(mock).not.toHaveBeenCalled()
  })

  it('网络失败保留幂等键：手动重试复用同一键并成功（无自动重试）', async () => {
    const keys: Array<string | null> = []
    let calls = 0
    mockFetch((_url, init) => {
      calls += 1
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      if (calls === 1) {
        throw new TypeError('Failed to fetch')
      }
      return jsonResponse(200, templateResponse())
    })
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'

    expect(await store.generate()).toBe(false)
    expect(store.errorTitle).toBe('连接不上服务器')
    expect(store.pendingGenerateKey).not.toBeNull()
    expect(calls).toBe(1)

    expect(await store.retryGenerate()).toBe(true)
    expect(calls).toBe(2)
    expect(keys[0]).toBe(keys[1])
    expect(keys[0]).not.toBeNull()
  })

  it('429 限流：记录 Retry-After 秒数、提示等待、不自动重试', async () => {
    const mock = mockFetch(() =>
      jsonResponse(
        429,
        { ok: false, error_code: 'OUT_OF_RANGE', message: '人物卡生成请求过于频繁' },
        { 'Retry-After': '30' },
      ),
    )
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'

    expect(await store.generate()).toBe(false)

    expect(store.retryAfterSeconds).toBe(30)
    expect(store.errorTitle).toBe('生成太频繁')
    expect(store.error).toContain('30 秒后重试')
    expect(store.busy).toBe(false)
    expect(mock).toHaveBeenCalledTimes(1) // 无自动重试
  })

  it('409 冲突：清幂等键，下次提交换新键', async () => {
    const keys: Array<string | null> = []
    let calls = 0
    mockFetch((_url, init) => {
      calls += 1
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      if (calls === 1) {
        return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '同键异参' })
      }
      return jsonResponse(200, templateResponse())
    })
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'

    expect(await store.generate()).toBe(false)
    expect(store.errorTitle).toBe('请求内容变了')
    expect(store.pendingGenerateKey).toBeNull()

    expect(await store.generate()).toBe(true)
    expect(keys[1]).not.toBe(keys[0])
  })

  it('失败分支文案：SCHEMA_VIOLATION / LLM_UNAVAILABLE / NOT_ALLOWED 均为中文引导', async () => {
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'

    mockFetch(() =>
      jsonResponse(422, { ok: false, error_code: 'SCHEMA_VIOLATION', message: 'schema bad' }),
    )
    await store.generate()
    expect(store.errorTitle).toBe('生成结果不完整')

    mockFetch(() =>
      jsonResponse(503, { ok: false, error_code: 'LLM_UNAVAILABLE', message: 'llm down' }),
    )
    await store.generate()
    expect(store.errorTitle).toBe('生成服务暂时不可用')

    mockFetch(() =>
      jsonResponse(403, { ok: false, error_code: 'NOT_ALLOWED', message: 'blocked' }),
    )
    await store.generate()
    expect(store.errorTitle).toBe('内容安全未通过')
    expect(store.errorCode).toBe('NOT_ALLOWED')
  })

  it('保存到卡库：POST /character-cards 带 9 字段与幂等键，成功后 canSave=false', async () => {
    const mock = mockFetch((url) => {
      if (url === '/api/v1/character-cards/generate') {
        return jsonResponse(200, templateResponse())
      }
      return jsonResponse(201, SAVED)
    })
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'
    await store.generate()

    const cardId = await store.save()

    expect(cardId).toBe(SAVED.card_id)
    expect(store.savedCardId).toBe(SAVED.card_id)
    expect(store.saveError).toBe('')
    expect(store.canSave).toBe(false)
    expect(String(callOf(mock, 1)[0])).toBe('/api/v1/character-cards')
    expect(bodyOf(mock, 1)).toEqual(CARD)
    expect(headersOf(mock, 1).get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('保存 409：清保存幂等键，下一次手动保存换新键', async () => {
    const keys: Array<string | null> = []
    let saveCalls = 0
    mockFetch((url, init) => {
      if (url === '/api/v1/character-cards/generate') {
        return jsonResponse(200, templateResponse())
      }
      saveCalls += 1
      keys.push(new Headers(init?.headers).get('Idempotency-Key'))
      if (saveCalls === 1) {
        return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '同键异参' })
      }
      return jsonResponse(201, SAVED)
    })
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'
    await store.generate()

    expect(await store.save()).toBeNull()
    expect(store.saveErrorTitle).toBe('保存内容变了')
    expect(store.pendingSaveKey).toBeNull()
    expect(store.canSave).toBe(true)

    expect(await store.save()).toBe(SAVED.card_id)
    expect(keys[1]).not.toBe(keys[0])
  })

  it('编辑 JSON 后保存禁用；本地 JSON 错误不发 validate 请求；修正后重新校验通过才能保存', async () => {
    let validateCalls = 0
    const mock = mockFetch((url) => {
      if (url === '/api/v1/character-cards/generate') {
        return jsonResponse(200, templateResponse())
      }
      if (url === '/api/v1/character-cards/validate') {
        validateCalls += 1
        return jsonResponse(200, { valid: true, errors: [] })
      }
      return jsonResponse(201, SAVED)
    })
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'
    await store.generate()
    expect(store.canSave).toBe(true)

    store.setEditedText('{not json')
    expect(store.canSave).toBe(false)
    expect(await store.revalidate()).toBe(false)
    expect(store.editedError).toContain('JSON 语法错误')
    expect(validateCalls).toBe(0)

    const edited = { ...CARD, name: '新名字旅人' }
    store.setEditedText(JSON.stringify(edited))
    expect(await store.revalidate()).toBe(true)
    expect(validateCalls).toBe(1)
    expect(store.canSave).toBe(true)
    expect(store.editedCard?.name).toBe('新名字旅人')

    // 保存的是重新校验后的内容（最后一次 fetch 是保存请求）
    await store.save()
    const lastCall = mock.mock.calls[mock.mock.calls.length - 1] as [RequestInfo | URL, RequestInit?]
    expect(JSON.parse(String(lastCall[1]?.body))).toEqual(edited)
  })

  it('重新校验未通过（服务端 valid=false）：保存保持禁用，并展示服务端 errors', async () => {
    mockFetch((url) => {
      if (url === '/api/v1/character-cards/generate') {
        return jsonResponse(200, templateResponse())
      }
      return jsonResponse(200, {
        valid: false,
        errors: [{ loc: ['hp'], type: 'value_error', msg: 'hp 必须小于等于生命上限' }],
      })
    })
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'
    await store.generate()

    store.setEditedText(JSON.stringify({ ...CARD, hp: 999 }))
    expect(await store.revalidate()).toBe(false)
    expect(store.editedValid).toBe(false)
    expect(store.editedErrors).toHaveLength(1)
    expect(store.canSave).toBe(false)
    expect(await store.save()).toBeNull()
    expect(store.saveErrorTitle).toBe('还没通过校验')
  })

  it('reset 清空生成状态（下次进入页面是干净表单）', async () => {
    mockFetch(() => jsonResponse(200, templateResponse()))
    const store = useCardGenerateStore()
    store.brief = '旧矿镇的拾荒者'
    await store.generate()

    store.reset()

    expect(store.draft).toBeNull()
    expect(store.brief).toBe('')
    expect(store.templateFallback).toBe(false)
    expect(store.editedText).toBe('')
    expect(GENERATE_IDEMPOTENCY_ACTION).toBe('card-generate')
    expect(SAVE_IDEMPOTENCY_ACTION).toBe('card-save')
  })
})
