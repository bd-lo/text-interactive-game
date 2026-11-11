import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ScenarioImportView from '@/views/ScenarioImportView.vue'

const STATS = { rooms: 2, items: 1, npcs: 1, objectives: 1, endings: 1, suggestions: 2 }

const DEFINITION = {
  schema_version: 1,
  slug: 'tiny-tower',
  meta: { title: '小塔' },
  player: { location: 'gate' },
  rooms: [{ key: 'gate', name: '塔门', description: '一扇虚掩的木门。' }],
}

const VALID_TEXT = JSON.stringify(DEFINITION)

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
      { path: '/scenarios', name: 'scenarios', component: { template: '<div>scenarios</div>' } },
      { path: '/scenarios/import', name: 'scenario-import', component: ScenarioImportView },
      {
        path: '/scenarios/:scenarioId',
        name: 'scenario-detail',
        component: { template: '<div>detail</div>' },
      },
    ],
  })
}

async function mountImport(handler: Handler) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  )
  vi.stubGlobal('fetch', mock)
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/scenarios/import')
  await router.isReady()
  const wrapper = mount(ScenarioImportView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router, mock }
}

function importButton(wrapper: Awaited<ReturnType<typeof mountImport>>['wrapper']) {
  return wrapper.findAll('button').find((button) => button.text().includes('确认导入'))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('场景导入页（/scenarios/import：先校验再导入）', () => {
  it('本地 JSON 语法错误不发请求；256KB 超限文件被拒绝（不做无谓上传）', async () => {
    const { wrapper, mock } = await mountImport(() =>
      jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' }),
    )

    await wrapper.find('textarea').setValue('{broken')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('JSON 语法错误')
    expect(mock).not.toHaveBeenCalled()

    const huge = new File(['x'.repeat(300 * 1024)], 'huge.json', { type: 'application/json' })
    await wrapper.find('.scenario-dropzone').trigger('drop', { dataTransfer: { files: [huge] } })
    await flushPromises()
    expect(wrapper.text()).toContain('文件过大')
    expect(wrapper.text()).toContain('256KB')
    expect(mock).not.toHaveBeenCalled()
  })

  it('文件类型/拖放：只接受 .json（非 JSON 文件给中文提示）', async () => {
    const { wrapper, mock } = await mountImport(() =>
      jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' }),
    )

    const txt = new File(['{}'], 'notes.txt', { type: 'text/plain' })
    const fileInput = wrapper.find('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', { value: [txt] })
    await fileInput.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('只接受 .json')
    expect(mock).not.toHaveBeenCalled()
  })

  it('校验未通过：逐条渲染 errors 的 JSON 路径与原因，导入按钮禁用', async () => {
    const { wrapper } = await mountImport((url) => {
      if (url === '/api/v1/scenarios/validate') {
        return jsonResponse(200, {
          ok: false,
          errors: [
            { path: 'rooms[2].exits.east', message: '未知房间 key "hub2"' },
            { path: 'meta.title', message: '不能为空' },
          ],
          warnings: [{ path: 'npcs[0].dialogue', message: '空 NPC 对话，将用通用文案' }],
          summary: STATS,
        })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await wrapper.find('textarea').setValue(VALID_TEXT)
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('校验未通过')
    expect(text).toContain('rooms[2].exits.east：未知房间 key "hub2"')
    expect(text).toContain('meta.title：不能为空')
    expect(text).toContain('npcs[0].dialogue：空 NPC 对话')
    expect(text).toContain('共 2 处问题')
    expect(importButton(wrapper)?.attributes('disabled')).toBeDefined()
  })

  it('校验通过：展示 summary，确认导入走 multipart + 幂等键并跳详情页', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const { wrapper, router } = await mountImport((url, init) => {
      calls.push({ url, init })
      if (url === '/api/v1/scenarios/validate') {
        return jsonResponse(200, { ok: true, errors: [], warnings: [], summary: STATS })
      }
      if (url === '/api/v1/scenarios' && init?.method === 'POST') {
        return jsonResponse(201, {
          scenario_id: 'scn_new',
          slug: 'tiny-tower',
          title: '小塔',
          summary: STATS,
          warnings: [{ path: 'items[0]', message: '未使用的物品 key' }],
        })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: `unexpected ${url}` })
    })

    await wrapper.find('textarea').setValue(VALID_TEXT)
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('校验通过')
    expect(wrapper.text()).toContain('2 个房间')
    expect(importButton(wrapper)?.attributes('disabled')).toBeUndefined()

    await importButton(wrapper)?.trigger('click')
    await flushPromises()

    const importCall = calls.find((call) => call.url === '/api/v1/scenarios')
    expect(importCall).toBeDefined()
    expect(importCall?.init?.body).toBeInstanceOf(FormData)
    const headers = new Headers(importCall?.init?.headers)
    expect(headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(new Headers(importCall?.init?.headers).get('Content-Type')).toBeNull()
    expect((importCall?.init?.body as FormData).get('file')).toBeInstanceOf(File)
    expect(router.currentRoute.value.name).toBe('scenario-detail')
    expect(router.currentRoute.value.params.scenarioId).toBe('scn_new')
  })

  it('导入失败：错误横幅展示玩家可读原因，停留在导入页可手动重试', async () => {
    const { wrapper, router } = await mountImport((url) => {
      if (url === '/api/v1/scenarios/validate') {
        return jsonResponse(200, { ok: true, errors: [], warnings: [], summary: STATS })
      }
      if (url === '/api/v1/scenarios') {
        return jsonResponse(409, { ok: false, error_code: 'CONFLICT', message: '同键异参' })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await wrapper.find('textarea').setValue(VALID_TEXT)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    await importButton(wrapper)?.trigger('click')
    await flushPromises()

    const banners = wrapper.findAll('.error-banner')
    expect(banners.some((banner) => banner.text().includes('操作重复了'))).toBe(true)
    expect(router.currentRoute.value.name).toBe('scenario-import')
  })

  it('编辑内容后旧校验结果作废（避免校验 A 导入 B）', async () => {
    const { wrapper } = await mountImport((url) => {
      if (url === '/api/v1/scenarios/validate') {
        return jsonResponse(200, { ok: true, errors: [], warnings: [], summary: STATS })
      }
      return jsonResponse(500, { ok: false, error_code: 'INTERNAL', message: 'unexpected' })
    })

    await wrapper.find('textarea').setValue(VALID_TEXT)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(importButton(wrapper)?.attributes('disabled')).toBeUndefined()

    await wrapper.find('textarea').setValue('{"schema_version":1}')
    await flushPromises()
    expect(wrapper.text()).toContain('还没有校验')
    expect(importButton(wrapper)?.attributes('disabled')).toBeDefined()
  })
})
