import { beforeEach, describe, expect, it } from 'vitest'

import {
  RECENT_GAMES_KEY,
  asScenario,
  describeRecentGame,
  forgetRecentGame,
  formatRelativeTime,
  loadRecentGames,
  rememberRecentGame,
} from '@/utils/recentGames'

function record(gameId: string, overrides: Partial<Parameters<typeof rememberRecentGame>[0]> = {}) {
  return {
    gameId,
    scenario: 'story',
    turn: 2,
    version: 2,
    lastText: '你在入口。',
    seed: 42,
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe('本机最近对局（localStorage 续玩兜底）', () => {
  it('remember/load：upsert 去重、按时间倒序、最多 8 条', () => {
    const now = new Date('2026-11-10T12:00:00Z')
    for (let index = 0; index < 10; index += 1) {
      rememberRecentGame(record(`g${index}`, { savedAt: new Date(now.getTime() + index * 1000).toISOString() }))
    }

    const loaded = loadRecentGames()
    expect(loaded).toHaveLength(8)
    expect(loaded[0]?.gameId).toBe('g9') // 最新在前
    expect(loaded.some((item) => item.gameId === 'g0')).toBe(false) // 最旧被淘汰

    rememberRecentGame(record('g9', { turn: 9, savedAt: new Date(now.getTime() + 20_000).toISOString() }), {
      now,
    })
    const updated = loadRecentGames()
    expect(updated.filter((item) => item.gameId === 'g9')).toHaveLength(1)
    expect(updated[0]?.gameId).toBe('g9')
    expect(updated[0]?.turn).toBe(9)
  })

  it('损坏/非数组数据静默丢弃，不影响首页渲染', () => {
    localStorage.setItem(RECENT_GAMES_KEY, '{not json')
    expect(loadRecentGames()).toEqual([])

    localStorage.setItem(RECENT_GAMES_KEY, JSON.stringify([{ gameId: '' }, { gameId: 'ok' }]))
    expect(loadRecentGames()).toEqual([])
  })

  it('forget：删除指定对局', () => {
    rememberRecentGame(record('g1'))
    rememberRecentGame(record('g2'))
    const left = forgetRecentGame('g1')
    expect(left.map((item) => item.gameId)).toEqual(['g2'])
  })

  it('相对时间与一行摘要可读', () => {
    const now = new Date('2026-11-10T12:00:00Z')
    expect(formatRelativeTime('2026-11-10T11:59:30Z', now)).toBe('刚刚')
    expect(formatRelativeTime('2026-11-10T11:52:00Z', now)).toBe('8 分钟前')
    // 用「相对 now 的小时数」构造，避免测试机时区（UTC+8）导致本地日期不同
    const yesterday = new Date(now.getTime() - 25 * 60 * 60 * 1000)
    expect(formatRelativeTime(yesterday.toISOString(), now)).toMatch(/^昨天 \d{2}:\d{2}$/)
    expect(
      describeRecentGame({ scenario: 'story', turn: 3, savedAt: '2026-11-10T11:52:00Z' }, {
        now,
        scenarioLabel: () => '余烬哨站（剧情）',
      }),
    ).toBe('余烬哨站（剧情） · 第 3 回合 · 8 分钟前')
  })

  it('asScenario：安全收窄服务端场景字符串', () => {
    expect(asScenario('story')).toBe('story')
    expect(asScenario('m4')).toBe('m4')
    expect(asScenario('future')).toBeNull()
  })
})
