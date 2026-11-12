import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import QuitGameControl from '@/components/QuitGameControl.vue'
import { EXIT_OVERLAY_BODY, EXIT_OVERLAY_TITLE, QUIT_INFO_TEXT } from '@/utils/launcher'

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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

function stubFetch(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

let mounted: VueWrapper | null = null

async function mountControl(handler: Handler) {
  const mock = stubFetch(handler)
  mounted = mount(QuitGameControl)
  await flushPromises()
  return { wrapper: mounted, mock }
}

function quitButton(wrapper: Awaited<ReturnType<typeof mountControl>>['wrapper']) {
  return wrapper.find('[data-testid="quit-game-button"]')
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('QuitGameControl（全局「退出游戏」交互状态机）', () => {
  it('status 请求失败：控件静默隐藏（不在全局 header 弹错误）', async () => {
    const { wrapper } = await mountControl(() =>
      jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'boom' }),
    )

    expect(quitButton(wrapper).exists()).toBe(false)
    expect(wrapper.find('.quit-error').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('退出游戏')
  })

  it('非启动器模式：点「退出游戏」直接弹固定 Ctrl+C 说明面板，知道了关闭后恢复', async () => {
    const { wrapper, mock } = await mountControl((url) =>
      url === '/api/v1/app/status'
        ? jsonResponse(200, PLAIN_STATUS)
        : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' }),
    )

    await quitButton(wrapper).trigger('click')
    const panel = wrapper.find('[data-testid="quit-info-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain(QUIT_INFO_TEXT)
    expect(panel.text()).toContain('当前不是启动器模式：请在运行服务的窗口按 Ctrl+C 退出。')

    const close = wrapper.find('[data-testid="quit-info-close"]')
    expect(close.text()).toBe('知道了')
    await close.trigger('click')

    expect(wrapper.find('[data-testid="quit-info-panel"]').exists()).toBe(false)
    expect(quitButton(wrapper).exists()).toBe(true)
    // 不支持页内退出时绝不发 shutdown 请求
    expect(
      mock.mock.calls.some((call) => String(call[0]) === '/api/v1/app/shutdown'),
    ).toBe(false)
  })

  it('启动器模式：确认/取消内联面板 → 确认后 POST shutdown 并立即显示已退出覆盖层', async () => {
    const shutdownCalls: Array<{ url: string; init?: RequestInit }> = []
    const { wrapper } = await mountControl((url, init) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/shutdown' && init?.method === 'POST') {
        shutdownCalls.push({ url, init })
        return jsonResponse(200, { ok: true, message: '游戏正在退出' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await quitButton(wrapper).trigger('click')
    const confirm = wrapper.find('[data-testid="quit-confirm-button"]')
    const cancel = wrapper.find('[data-testid="quit-cancel-button"]')
    expect(confirm.text()).toBe('确认退出（进度已自动保存）')
    expect(cancel.text()).toBe('取消')

    // 取消回到 idle：不调 shutdown
    await cancel.trigger('click')
    expect(shutdownCalls).toHaveLength(0)
    expect(quitButton(wrapper).exists()).toBe(true)

    // 再次确认：调 POST shutdown（body confirm:true）→ 覆盖层
    await quitButton(wrapper).trigger('click')
    await wrapper.find('[data-testid="quit-confirm-button"]').trigger('click')
    await flushPromises()

    expect(shutdownCalls).toHaveLength(1)
    expect(JSON.parse(String(shutdownCalls[0]?.init?.body))).toEqual({ confirm: true })

    const overlay = wrapper.find('[data-testid="exit-overlay"]')
    expect(overlay.exists()).toBe(true)
    expect(overlay.attributes('role')).toBe('status')
    expect(overlay.attributes('aria-live')).toBe('polite')
    expect(overlay.text()).toContain(EXIT_OVERLAY_TITLE)
    expect(overlay.text()).toContain(EXIT_OVERLAY_BODY)
    expect(overlay.text()).toContain('游戏已退出')
    expect(overlay.text()).toContain('可以关闭此页面了。')
    // 已退出后 header 不再显示退出按钮
    expect(quitButton(wrapper).exists()).toBe(false)
  })

  it('shutdown 失败（403）：展示玩家可读错误、覆盖层不出现、按钮恢复可点且无自动重试', async () => {
    let shutdownCalls = 0
    const { wrapper } = await mountControl((url, init) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/shutdown' && init?.method === 'POST') {
        shutdownCalls += 1
        return jsonResponse(403, {
          ok: false,
          error_code: 'NOT_ALLOWED',
          message: '只允许本机回环地址退出游戏',
        })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await quitButton(wrapper).trigger('click')
    await wrapper.find('[data-testid="quit-confirm-button"]').trigger('click')
    await flushPromises()

    const error = wrapper.find('.quit-error')
    expect(error.exists()).toBe(true)
    expect(error.attributes('role')).toBe('alert')
    expect(error.text()).toContain('只允许本机回环地址退出游戏')
    expect(wrapper.find('[data-testid="exit-overlay"]').exists()).toBe(false)
    expect(quitButton(wrapper).exists()).toBe(true)
    expect(shutdownCalls).toBe(1)

    // 无自动重试：再等一段（真实计时器）也不会自行重发
    await new Promise((resolve) => setTimeout(resolve, 20))
    await flushPromises()
    expect(shutdownCalls).toBe(1)
  })

  it('启动器 status 只加载一次（header 常驻，不轮询）', async () => {
    const { wrapper, mock } = await mountControl((url) =>
      url === '/api/v1/app/status'
        ? jsonResponse(200, LAUNCHER_STATUS)
        : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' }),
    )

    const statusCalls = mock.mock.calls.filter(
      (call) => String(call[0]) === '/api/v1/app/status',
    )
    expect(statusCalls).toHaveLength(1)
    // 普通状态下按钮文案固定
    expect(quitButton(wrapper).text()).toBe('退出游戏')
  })
})

describe('QuitGameControl（041 a11y：确认面板焦点管理）', () => {
  /** 焦点断言需要真实挂到 document 上（jsdom 对游离节点不更新 activeElement）。 */
  async function mountAttached(handler: Handler) {
    stubFetch(handler)
    const wrapper = mount(QuitGameControl, { attachTo: document.body })
    mounted = wrapper
    await flushPromises()
    return wrapper
  }

  it('打开确认面板即聚焦「确认退出」；Esc 取消后焦点归还「退出游戏」按钮', async () => {
    const wrapper = await mountAttached((url) =>
      url === '/api/v1/app/status'
        ? jsonResponse(200, LAUNCHER_STATUS)
        : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' }),
    )

    await quitButton(wrapper).trigger('click')
    await flushPromises()
    const confirm = wrapper.find('[data-testid="quit-confirm-button"]')
    expect(document.activeElement).toBe(confirm.element)

    await wrapper.find('.quit-game-control').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('[data-testid="quit-confirm-button"]').exists()).toBe(false)
    expect(document.activeElement).toBe(quitButton(wrapper).element)
  })

  it('非启动器说明面板：打开聚焦「知道了」，Esc 关闭后焦点归还', async () => {
    const wrapper = await mountAttached((url) =>
      url === '/api/v1/app/status'
        ? jsonResponse(200, PLAIN_STATUS)
        : jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' }),
    )

    await quitButton(wrapper).trigger('click')
    await flushPromises()
    const close = wrapper.find('[data-testid="quit-info-close"]')
    expect(document.activeElement).toBe(close.element)

    await wrapper.find('.quit-game-control').trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('[data-testid="quit-info-panel"]').exists()).toBe(false)
    expect(document.activeElement).toBe(quitButton(wrapper).element)
  })

  it('已退出覆盖层：tabindex=-1 接管焦点，标题用 aria-labelledby 关联', async () => {
    const wrapper = await mountAttached((url, init) => {
      if (url === '/api/v1/app/status') {
        return jsonResponse(200, LAUNCHER_STATUS)
      }
      if (url === '/api/v1/app/shutdown' && init?.method === 'POST') {
        return jsonResponse(200, { ok: true, message: '游戏正在退出' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await quitButton(wrapper).trigger('click')
    await wrapper.find('[data-testid="quit-confirm-button"]').trigger('click')
    await flushPromises()

    const overlay = wrapper.find('[data-testid="exit-overlay"]')
    const title = overlay.find('h2')
    expect(overlay.attributes('tabindex')).toBe('-1')
    expect(overlay.attributes('aria-labelledby')).toBe(title.attributes('id'))
    expect(document.activeElement).toBe(overlay.element)
  })
})
