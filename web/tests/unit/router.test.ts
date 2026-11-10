import { describe, expect, it } from 'vitest'

import router from '@/router'

describe('路由表（懒加载 + 404 兜底）', () => {
  it('已定义对局/回放/卡库/404 路由', () => {
    expect(router.hasRoute('home')).toBe(true)
    expect(router.hasRoute('game')).toBe(true)
    expect(router.hasRoute('replay')).toBe(true)
    expect(router.hasRoute('cards')).toBe(true)
    expect(router.hasRoute('not-found')).toBe(true)
  })

  it('未知路径落到 404 页面而不是空白', async () => {
    await router.push('/definitely-not-a-page')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('not-found')
  })

  it('对局深链匹配 /game/:id/replay', () => {
    const resolved = router.resolve('/game/game_123/replay')
    expect(resolved.name).toBe('replay')
    expect(resolved.params.id).toBe('game_123')
  })

  it('M6-028 /cards/generate 命中生成页，而不是被 /cards/:cardId 详情吞掉', () => {
    expect(router.hasRoute('card-generate')).toBe(true)
    expect(router.resolve('/cards/generate').name).toBe('card-generate')
    expect(router.resolve('/cards/card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK').name).toBe('card-detail')
  })
})
