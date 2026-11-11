import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClientError,
  getAppStatus,
  LAUNCHER_HEARTBEAT_TIMEOUT_MS,
  LAUNCHER_SHUTDOWN_TIMEOUT_MS,
  LAUNCHER_STATUS_TIMEOUT_MS,
  sendLauncherHeartbeat,
  shutdownGame,
} from '@/api/client'

const STATUS = {
  launcher_mode: true,
  idle_exit_seconds: 300,
  shutdown_supported: true,
  version: '0.2.0',
}

const HEARTBEAT = { ok: true, idle_exit_seconds: 300 }
const SHUTDOWN = { ok: true, message: '游戏正在退出' }

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type FetchHandler = (url: string, init?: RequestInit) => Response | Promise<Response>

function mockFetch(handler: FetchHandler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('启动器控制面 API client（status / heartbeat / shutdown）', () => {
  it('getAppStatus：GET 解析 4 字段；响应字段漂移 → 契约错误', async () => {
    const mock = mockFetch(() => jsonResponse(200, STATUS))

    const status = await getAppStatus()

    expect(status.launcher_mode).toBe(true)
    expect(status.idle_exit_seconds).toBe(300)
    expect(status.shutdown_supported).toBe(true)
    expect(status.version).toBe('0.2.0')
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/app/status')
    expect(call[1]?.method).toBe('GET')
    expect(call[1]?.body).toBeUndefined()

    mockFetch(() => jsonResponse(200, { launcher_mode: false }))
    const error = (await getAppStatus().catch((reason: unknown) => reason)) as ApiClientError
    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.message).toContain('契约')
  })

  it('非启动器模式 status 仍 200（launcher_mode/shutdown_supported=false, idle=0）', async () => {
    mockFetch(() =>
      jsonResponse(200, {
        launcher_mode: false,
        idle_exit_seconds: 0,
        shutdown_supported: false,
        version: '0.2.0',
      }),
    )

    const status = await getAppStatus()

    expect(status.launcher_mode).toBe(false)
    expect(status.shutdown_supported).toBe(false)
    expect(status.idle_exit_seconds).toBe(0)
  })

  it('sendLauncherHeartbeat：POST 无 body、无 Idempotency-Key/Content-Type，解析心跳响应', async () => {
    const mock = mockFetch(() => jsonResponse(200, HEARTBEAT))

    const beat = await sendLauncherHeartbeat()

    expect(beat.ok).toBe(true)
    expect(beat.idle_exit_seconds).toBe(300)
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/app/heartbeat')
    expect(call[1]?.method).toBe('POST')
    expect(call[1]?.body).toBeUndefined()
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Idempotency-Key')).toBeNull() // 心跳无领域副作用，不需要幂等键
    expect(headers.get('Content-Type')).toBeNull()
  })

  it('heartbeat 无控制器（404 信封）→ ApiClientError NOT_FOUND', async () => {
    mockFetch(() =>
      jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: 'launcher controller 不存在' }),
    )

    const error = (await sendLauncherHeartbeat().catch((reason: unknown) => reason)) as ApiClientError

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error.status).toBe(404)
    expect(error.code).toBe('NOT_FOUND')
  })

  it('shutdownGame：POST body {confirm:true} + JSON Content-Type，无幂等键，解析 200', async () => {
    const bodies: unknown[] = []
    const mock = mockFetch((_url, init) => {
      bodies.push(JSON.parse(String(init?.body)))
      return jsonResponse(200, SHUTDOWN)
    })

    const result = await shutdownGame()

    expect(result.ok).toBe(true)
    expect(result.message).toBe('游戏正在退出')
    expect(bodies).toEqual([{ confirm: true }])
    const call = mock.mock.calls[0] as [RequestInfo | URL, RequestInit | undefined]
    expect(String(call[0])).toBe('/api/v1/app/shutdown')
    expect(call[1]?.method).toBe('POST')
    const headers = new Headers(call[1]?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Idempotency-Key')).toBeNull()
  })

  it('shutdown 失败：非回环 403 / 无控制器 404 都抛 ApiClientError（调用方展示，不自动重试）', async () => {
    mockFetch(() =>
      jsonResponse(403, {
        ok: false,
        error_code: 'NOT_ALLOWED',
        message: '只允许本机回环地址退出游戏',
      }),
    )
    const forbidden = (await shutdownGame().catch((reason: unknown) => reason)) as ApiClientError
    expect(forbidden).toBeInstanceOf(ApiClientError)
    expect(forbidden.status).toBe(403)
    expect(forbidden.code).toBe('NOT_ALLOWED')

    let calls = 0
    mockFetch(() => {
      calls += 1
      return jsonResponse(404, { ok: false, error_code: 'NOT_FOUND', message: 'no controller' })
    })
    const notFound = (await shutdownGame().catch((reason: unknown) => reason)) as ApiClientError
    expect(notFound.status).toBe(404)
    expect(calls).toBe(1) // 无自动重试
  })

  it('契约超时：状态/心跳 5s、shutdown 8s，超时抛 ApiClientError(TIMEOUT) 且不自动重试', async () => {
    expect(LAUNCHER_STATUS_TIMEOUT_MS).toBe(5_000)
    expect(LAUNCHER_HEARTBEAT_TIMEOUT_MS).toBe(5_000)
    expect(LAUNCHER_SHUTDOWN_TIMEOUT_MS).toBe(8_000)

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        calls += 1
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        })
      }),
    )

    const pendingStatus = getAppStatus().catch((reason: unknown) => reason)
    await vi.advanceTimersByTimeAsync(LAUNCHER_STATUS_TIMEOUT_MS)
    const statusError = (await pendingStatus) as ApiClientError
    expect(statusError).toBeInstanceOf(ApiClientError)
    expect(statusError.code).toBe('TIMEOUT')
    expect(calls).toBe(1)

    const pendingShutdown = shutdownGame().catch((reason: unknown) => reason)
    await vi.advanceTimersByTimeAsync(LAUNCHER_SHUTDOWN_TIMEOUT_MS)
    const shutdownError = (await pendingShutdown) as ApiClientError
    expect(shutdownError.code).toBe('TIMEOUT')
    expect(calls).toBe(2)
  })
})
