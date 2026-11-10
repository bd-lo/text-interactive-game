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
  scenario: 'story',
  ending_text: null,
  world_book: null,
  world_book_id: null,
  lore: null,
}
const GAME_VIEW_2 = { ...GAME_VIEW_1, turn: 2, version: 2, snapshot_version: 2, last_text: '你走进地窖。' }
const GAME_VIEW_3 = { ...GAME_VIEW_1, turn: 3, version: 3, snapshot_version: 3, last_text: '你继续前进。' }
const GAME_VIEW_4 = { ...GAME_VIEW_1, turn: 4, version: 4, snapshot_version: 4, last_text: '别处推进的回合。' }

const PLAYER_VIEW_1 = {
  game_id: 'g1',
  turn: 1,
  version: 1,
  snapshot_version: 1,
  status: 'active',
  scenario: 'story',
  hp: 20,
  max_hp: 20,
  inventory: [],
  room: { room_id: 'entry', title: '入口', description: '一间石室。', exits: {}, items: [], npcs: [] },
  goal: null,
  ending_text: null,
  suggested_actions: [],
  world_book: null,
  lore: null,
}
const PLAYER_VIEW_2 = { ...PLAYER_VIEW_1, turn: 2, version: 2, snapshot_version: 2 }
const PLAYER_VIEW_3 = { ...PLAYER_VIEW_1, turn: 3, version: 3, snapshot_version: 3 }
const PLAYER_VIEW_4 = { ...PLAYER_VIEW_1, turn: 4, version: 4, snapshot_version: 4 }

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
  narrative_source: 'template',
}

/** replay 事件（`GET /games/{id}/replay` 契约字段；供历史重建测试）。 */
function replayEvent(seq: number, turnId: number, narrative?: string, actorId = 'char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN') {
  return {
    id: `evt-${seq}`,
    seq,
    turn_id: turnId,
    actor_id: actorId,
    method: 'move_to',
    event_type: 'CHARACTER_MOVED',
    params: {},
    result: narrative === undefined ? {} : { narrative },
    rng_seed: 42,
    ts: '2026-11-10T15:00:00Z',
  }
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
  localStorage.clear()
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
    // 包 A：成功回合即时追加冒险记录（与刷新后 replay 重建同源）
    expect(store.history).toHaveLength(1)
    expect(store.history[0]?.text).toBe(TURN_OK.text)
  })

  it('载入对局：用只读 replay 重建冒险记录（无叙事事件/同回合多事件去重）', async () => {
    const replayUrls: string[] = []
    mockFetch((url) => {
      if (url === '/api/v1/games/g1') return jsonResponse(200, GAME_VIEW_3)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_3)
      if (url.startsWith('/api/v1/games/g1/replay')) {
        replayUrls.push(url)
        return jsonResponse(200, {
          events: [
            replayEvent(1, 1), // game_created：无叙事
            replayEvent(2, 2, '你捡起了火把。'),
            replayEvent(3, 3, '你向北走进地窖。'),
            replayEvent(4, 3, '你向北走进地窖。', 'char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV'), // 同回合 NPC 事件
          ],
          from_seq: 1,
          to_seq: 4,
        })
      }
      return unexpected(url)
    })
    const store = useGameStore()

    await store.loadGame('g1')

    expect(replayUrls).toHaveLength(1)
    expect(replayUrls[0]).toContain('/api/v1/games/g1/replay?from_seq=1&limit=500')
    expect(store.history.map((entry) => entry.text)).toEqual([
      '你捡起了火把。',
      '你向北走进地窖。',
    ])
    expect(store.history.every((entry) => entry.kind === 'narrative')).toBe(true)
    expect(store.historyLoading).toBe(false)
    expect(store.historyError).toBe('')
  })

  it('载入对局：replay 失败不阻塞游戏（historyError 提示，view 仍可用）', async () => {
    mockFetch((url) => {
      if (url === '/api/v1/games/g1') return jsonResponse(200, GAME_VIEW_3)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_3)
      if (url.startsWith('/api/v1/games/g1/replay')) return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
      return unexpected(url)
    })
    const store = useGameStore()

    await store.loadGame('g1')

    expect(store.view?.turn).toBe(3)
    expect(store.history).toEqual([])
    expect(store.historyError).not.toBe('')
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

  it('失败回合：effects hint / suggested_actions 进 store（供分组展示），不覆盖 text 原文流', async () => {
    const failed = {
      ...TURN_OK,
      ok: false,
      turn_id: null,
      text: '这个动作没有生效',
      events: [],
      error_code: 'INVALID_INPUT',
      effects: [{ hint: '这个动作没有被理解或当前无法执行，可以换个说法再试。' }],
      suggested_actions: ['环顾四周', '查看背包'],
    }
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_1)
      if (url === '/api/v1/turns') return jsonResponse(200, failed)
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'm2' })
    await store.submitTurn('乱按')

    expect(store.lastEffects).toEqual(failed.effects)
    expect(store.lastSuggestedActions).toEqual(failed.suggested_actions)
    expect(store.errorCode).toBe('INVALID_INPUT')
    expect(store.lastText).toBe('') // 失败不覆盖文本流原文（TurnTextStream 只展示 text）
    // 包 A：失败回合在冒险记录里追加系统行，不覆盖叙事
    expect(store.history).toHaveLength(1)
    expect(store.history[0]?.kind).toBe('notice')
    expect(store.history[0]?.text).toBe('（该指令未生效）')
  })

  it('业务失败 CONFLICT（HTTP 200 + ok=false）→ 用服务端 version 自愈并刷新版本链', async () => {
    let turnCalls = 0
    let viewVersion = 1
    let gameVersion = 1
    const expectedVersions: number[] = []
    mockFetch((url, init) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1') {
        return jsonResponse(200, gameVersion === 1 ? GAME_VIEW_1 : GAME_VIEW_4)
      }
      if (url === '/api/v1/games/g1/view') {
        return jsonResponse(200, viewVersion === 1 ? PLAYER_VIEW_1 : PLAYER_VIEW_4)
      }
      if (url.startsWith('/api/v1/games/g1/replay')) {
        return jsonResponse(200, { events: [], from_seq: 1, to_seq: 0 })
      }
      if (url === '/api/v1/turns') {
        turnCalls += 1
        expectedVersions.push((JSON.parse(String(init?.body)) as { expected_version: number }).expected_version)
        viewVersion = 4
        gameVersion = 4
        return jsonResponse(200, {
          ...TURN_OK,
          ok: false,
          turn_id: null,
          version: 4,
          text: '你的对局版本已经过期，请刷新对局视图后重试。',
          error_code: 'CONFLICT',
        })
      }
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'story' })
    await store.submitTurn('向北走')

    // 包 C F2：不再要求玩家自己找「刷新版本链」，用服务端回传的最新 version 自愈
    expect(turnCalls).toBe(1)
    expect(store.errorCode).toBe('CONFLICT')
    expect(store.version).toBe(4)
    expect(store.turn).toBe(4)
    expect(store.canRetry).toBe(true)
    expect(store.errorTitle).toContain('别处')
    expect(store.error).toContain('已同步到第 4 回合')
    expect(store.errorDetail).toContain('CONFLICT')
    expect(store.history.some((entry) => entry.kind === 'notice')).toBe(true)
    expect(expectedVersions).toEqual([1])
  })

  it('成功回合后 /view 刷新失败：视为已提交，不提供重试（防重复执行）', async () => {
    let turnCalls = 0
    let viewCalls = 0
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') {
        viewCalls += 1
        // 创建时刷新成功；成功回合后的刷新失败（模拟网络抖动）
        return viewCalls === 1
          ? jsonResponse(200, PLAYER_VIEW_1)
          : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
      }
      if (url === '/api/v1/turns') {
        turnCalls += 1
        return jsonResponse(200, TURN_OK)
      }
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'story' })
    await store.submitTurn('向北走')

    expect(turnCalls).toBe(1)
    expect(store.errorTitle).toBe('回合已保存')
    expect(store.canRetry).toBe(false) // 绝不提示重试（否则会用新键重复执行）
    expect(store.history.map((entry) => entry.text)).toEqual([TURN_OK.text])

    await store.retry() // 无 canRetry：必须是 no-op
    expect(turnCalls).toBe(1)
  })

  it('叙事来源徽章：优先读 TurnResult.narrative_source（无事件回合也能标来源）', async () => {
    const withSource = { ...TURN_OK, events: [], narrative_source: 'llm' as const }
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      if (url === '/api/v1/turns') return jsonResponse(200, withSource)
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'story' })
    expect(store.narrativeSource).toBe('')
    await store.submitTurn('查看背包')

    expect(store.narrativeSource).toBe('llm')
  })

  it('叙事来源徽章：narrative_source 缺失/为 null 时回退事件里的 result.narrative_source', async () => {
    const legacy = {
      ...TURN_OK,
      narrative_source: null,
      events: [
        {
          turn_id: 2,
          actor_id: 'char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN',
          method: 'move_to',
          params: {},
          result: { narrative_source: 'llm' },
        },
      ],
    }
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') return jsonResponse(200, PLAYER_VIEW_2)
      if (url === '/api/v1/turns') return jsonResponse(200, legacy)
      return unexpected(url)
    })
    const store = useGameStore()

    await store.createGame({ seed: 42, scenario: 'story' })
    await store.submitTurn('向北走')

    expect(store.narrativeSource).toBe('llm')
  })

  it('创建成功但首次状态拉取失败：仍返回 game_id（不阻塞进入对局）', async () => {
    mockFetch((url) => {
      if (url === '/api/v1/games') return jsonResponse(201, CREATED)
      if (url === '/api/v1/games/g1/view') {
        return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' })
      }
      return unexpected(url)
    })
    const store = useGameStore()

    const gameId = await store.createGame({ seed: 42, scenario: 'story' })

    expect(gameId).toBe('g1')
    expect(store.errorTitle).toBe('对局已创建')
    expect(store.view).toBeNull()
  })
})
