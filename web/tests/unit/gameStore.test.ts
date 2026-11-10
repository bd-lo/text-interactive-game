import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useGameStore } from '@/stores/game'

const CREATED = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  save_seed: '42',
}

const GAME_VIEW_1 = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  status: 'active',
  last_text: '你在入口。',
}
const GAME_VIEW_2 = { ...GAME_VIEW_1, turn: 2, version: 2, snapshot_version: 2, last_text: '你走进地窖。' }
const GAME_VIEW_3 = { ...GAME_VIEW_1, turn: 3, version: 3, snapshot_version: 3, last_text: '你继续前进。' }

const PLAYER_VIEW_1 = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  status: 'active',
  hp: 20,
  max_hp: 20,
  inventory: [],
  room: { room_id: 'entry', title: '入口', description: '一间石室。', exits: {}, items: [], npcs: [] },
}
const PLAYER_VIEW_2 = { ...PLAYER_VIEW_1, turn: 2, version: 2, snapshot_version: 2 }
const PLAYER_VIEW_3 = { ...PLAYER_VIEW_1, turn: 3, version: 3, snapshot_version: 3 }

const TURN_OK = {
  ok: true,
  turn_id: 2,
  version: 2,
  text: '你向北走进地窖。',
  events: [],
  error_code: null,
  effects: [],
  suggested_actions: [],
  usage: {},
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init))
  vi.stubGlobal('fetch', mock)
  return mock
}

function unexpected(url: string): Response {
  return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('game store（服务端权威版本链 + 禁自动重试）', () => {
  it('createGame 后用服务端返回初始化 version/turn，并拉取状态面板', async () => {
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_1)
      return unexpected(url)
    })
    const store = useGameStore()

    const gameId = await store.createGame({ seed: 42, scenario: 'm2' })

    expect(gameId).toBe('g1')
    expect(store.gameId).toBe('g1')
    expect(store.version).toBe(1)
    expect(store.turn).toBe(1)
    expect(store.view?.hp).toBe(20)
  })

  it('回合成功后用 TurnResult.version 覆盖本地（禁止本地自增）', async () => {
    let viewVersion = 1
    let gameVersion = 1
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1') return jsonResponse(200, gameVersion === 1 ? GAME_VIEW_1 : GAME_VIEW_2)
      if (url === '/api/v1/games/g1/view') {
        return jsonResponse(200, viewVersion === 1 ? PLAYER_VIEW_1 : PLAYER_VIEW_2)
      }
      if (url === '/api/v1/turns') {
        viewVersion = 2
        gameVersion = 2
        return jsonResponse(200, TURN_OK)
      }
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'm2' })
    await store.submitTurn('向北走')

    expect(store.version).toBe(2)
    expect(store.turn).toBe(2)
    expect(store.lastText).toBe(TURN_OK.text)
    expect(store.error).toBe('')
  })

  it('网络失败：不自动重试，保留幂等键；手动 retry 复用同键并成功', async () => {
    let turnCalls = 0
    let viewVersion = 1
    let gameVersion = 1
    const keys: Array<string | null> = []
    mockFetch((url, init) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1') return jsonResponse(200, gameVersion === 1 ? GAME_VIEW_1 : GAME_VIEW_2)
      if (url === '/api/v1/games/g1/view') {
        return jsonResponse(200, viewVersion === 1 ? PLAYER_VIEW_1 : PLAYER_VIEW_2)
      }
      if (url === '/api/v1/turns') {
        turnCalls += 1
        keys.push(new Headers(init?.headers).get('Idempotency-Key'))
        if (turnCalls === 1) {
          throw new TypeError('fetch failed')
        }
        viewVersion = 2
        gameVersion = 2
        return jsonResponse(200, TURN_OK)
      }
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'm2' })
    await store.submitTurn('向北走')

    expect(turnCalls).toBe(1) // 没有自动重试
    expect(store.canRetry).toBe(true)
    expect(store.error).not.toBe('')

    await store.retry()

    expect(turnCalls).toBe(2) // 仅用户手动触发
    expect(keys[1]).toBe(keys[0]) // 手动重试复用同一幂等键
    expect(store.version).toBe(2)
  })

  it('CONFLICT：刷新服务端 version、换新键后由用户重提', async () => {
    let turnCalls = 0
    let viewVersion = 1
    let gameVersion = 1
    const keys: Array<string | null> = []
    const expectedVersions: number[] = []
    mockFetch((url, init) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1') {
        if (gameVersion === 1) return jsonResponse(200, GAME_VIEW_1)
        return jsonResponse(200, gameVersion === 2 ? GAME_VIEW_2 : GAME_VIEW_3)
      }
      if (url === '/api/v1/games/g1/view') {
        if (viewVersion === 1) return jsonResponse(200, PLAYER_VIEW_1)
        return jsonResponse(200, viewVersion === 2 ? PLAYER_VIEW_2 : PLAYER_VIEW_3)
      }
      if (url === '/api/v1/turns') {
        turnCalls += 1
        keys.push(new Headers(init?.headers).get('Idempotency-Key'))
        expectedVersions.push((JSON.parse(String(init?.body)) as { expected_version: number }).expected_version)
        if (turnCalls === 1) {
          viewVersion = 2
          gameVersion = 2
          return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '版本冲突' })
        }
        viewVersion = 3
        gameVersion = 3
        return jsonResponse(200, { ...TURN_OK, turn_id: 3, version: 3, text: '你继续前进。' })
      }
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'm2' })
    await store.submitTurn('向北走')

    expect(store.errorCode).toBe('CONFLICT')
    expect(store.version).toBe(2) // 已从 GET /games/{id} 刷新
    expect(store.canRetry).toBe(true)

    await store.retry()

    expect(turnCalls).toBe(2)
    expect(keys[1]).not.toBe(keys[0]) // CONFLICT 后换新键
    expect(expectedVersions[1]).toBe(2) // 用刷新后的服务端 version
    expect(store.version).toBe(3)
  })
})
