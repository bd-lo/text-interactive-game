import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import QuitGameControl from '@/components/QuitGameControl.vue'
import { LAUNCHER_HEARTBEAT_INTERVAL_MS } from '@/utils/launcher'

const LAUNCHER_STATUS = {
  launcher_mode: true,
  idle_exit_seconds: 300,
  shutdown_supported: true,
  version: '0.2.0',
}

const PLAIN_STATUS = {
  launcher_mode: false,
  idle_exit_seconds: 0,
  shutdown_supported: false,
  version: '0.2.0',
}

const HEARTBEAT = { ok: true, idle_exit_seconds: 300 }

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

let mounted: VueWrapper | null = null

function stubFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

async function mountControl(handler: Handler) {
  const mock = stubFetch(handler)
  mounted = mount(QuitGameControl)
  await flushPromises()
  return { wrapper: mounted, mock }
}

function heartbeatCalls(mock: ReturnType<typeof stubFetch>) {
  return mock.mock.calls.filter((call) => String(call[0]) === '/api/v1/app/heartbeat')
}

function statusHandler(status: unknown): Handler {
  return (url) =>
    url === '/api/v1/app/status'
      ? jsonResponse(200, status)
      : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
}

beforeEach(() => {
  // 只伪造 timer API（含 setInterval 心跳节拍）；flushPromises 依赖真实 setImmediate，不会被卡住
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('QuitGameControl 心跳（launcher_mode=true 每 15s 一次，fire-and-forget）', () => {
  it('启动器模式：首拍不早于 15s，之后每 15s 一次 POST /heartbeat', async () => {
    const { mock } = await mountControl((url) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/heartbeat') {
        return jsonResponse(200, HEARTBEAT)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    expect(heartbeatCalls(mock)).toHaveLength(0)

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS)
    await flushPromises()
    const first = heartbeatCalls(mock)
    expect(first).toHaveLength(1)
    expect(String(first[0]?.[0])).toBe('/api/v1/app/heartbeat')
    expect((first[0]?.[1] as RequestInit | undefined)?.method).toBe('POST')

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS)
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(2)

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS)
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(3)
  })

  it('非启动器模式：不发心跳（只读取一次 status），页面可见事件也不补拍', async () => {
    const { mock } = await mountControl(statusHandler(PLAIN_STATUS))

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS * 4)
    await flushPromises()
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()

    expect(heartbeatCalls(mock)).toHaveLength(0)
    expect(
      mock.mock.calls.filter((call) => String(call[0]) === '/api/v1/app/status'),
    ).toHaveLength(1)
  })

  it('页面重新可见时立即补一拍心跳（后台 timer 节流兜底）；隐藏时不发', async () => {
    const { mock } = await mountControl((url) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/heartbeat') {
        return jsonResponse(200, HEARTBEAT)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    // 挂载后尚未到 15s：不应有心跳
    expect(heartbeatCalls(mock)).toHaveLength(0)

    // 切回可见：立即补一拍
    const visibilitySpy = vi
      .spyOn(Document.prototype, 'visibilityState', 'get')
      .mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(1)

    // 页面隐藏：补拍事件不发
    visibilitySpy.mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(1)

    visibilitySpy.mockRestore()
  })

  it('心跳失败静默忽略（禁止 console.error），下一拍继续发', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { mock } = await mountControl((url) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/heartbeat') {
        return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'controller boom' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS * 2)
    await flushPromises()

    expect(heartbeatCalls(mock)).toHaveLength(2)
    expect(consoleError).not.toHaveBeenCalled()
    // 心跳失败不把控件打挂：退出按钮仍在
    expect(mounted?.find('[data-testid="quit-game-button"]').exists()).toBe(true)
  })

  it('组件卸载后停止心跳（无泄漏定时器）', async () => {
    const { wrapper, mock } = await mountControl((url) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/heartbeat') {
        return jsonResponse(200, HEARTBEAT)
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS)
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(1)

    wrapper.unmount()
    mounted = null
    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS * 3)
    await flushPromises()
    // 卸载后既没有定时心跳，也不再监听 visibilitychange
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(1)
  })

  it('成功退出后停止心跳（已退出覆盖层期间不再发）', async () => {
    const { wrapper, mock } = await mountControl((url, init) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/heartbeat') {
        return jsonResponse(200, HEARTBEAT)
      }
      if (url === '/api/v1/app/shutdown' && init?.method === 'POST') {
        return jsonResponse(200, { ok: true, message: '游戏正在退出' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await wrapper.find('[data-testid="quit-game-button"]').trigger('click')
    await wrapper.find('[data-testid="quit-confirm-button"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="exit-overlay"]').exists()).toBe(true)

    await vi.advanceTimersByTimeAsync(LAUNCHER_HEARTBEAT_INTERVAL_MS * 3)
    await flushPromises()
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(heartbeatCalls(mock)).toHaveLength(0)
  })
})
