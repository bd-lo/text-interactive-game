/**
 * N1-031 场景编辑器视图：默认表单、实时校验（debounce）、错误展示、JSON 双向同步、
 * v1 载入提示、保存幂等键与跳转、下载导出。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ScenarioEditorView from '@/views/ScenarioEditorView.vue'
import { useScenariosStore } from '@/stores/scenarios'

const OK_SUMMARY = { rooms: 1, items: 0, npcs: 0, objectives: 0, endings: 0, suggestions: 0 }

const V1_DETAIL = {
  scenario_id: 'scn_old',
  slug: 'tiny-tower',
  title: '小塔',
  author: '示例',
  description: '两房间最小可玩场景',
  schema_version: 1,
  created_at: '2026-11-12T00:00:00Z',
  definition: {
    schema_version: 1,
    slug: 'tiny-tower',
    meta: { title: '小塔', author: '示例' },
    player: { location: 'gate', hp: 10, max_hp: 10 },
    rooms: [{ key: 'gate', name: '塔门', description: '一扇虚掩的木门。' }],
  },
  summary: OK_SUMMARY,
  warnings: [],
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/scenarios', name: 'scenarios', component: { template: '<div>scenarios</div>' } },
      {
        path: '/scenarios/editor',
        name: 'scenario-editor',
        component: ScenarioEditorView,
      },
      {
        path: '/scenarios/:scenarioId',
        name: 'scenario-detail',
        component: { template: '<div>detail</div>' },
      },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

/** 等待 600ms debounce + 微任务落定（真实计时器；每个用例最多 0.7s）。 */
async function waitForDebounce(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 700))
  await flushPromises()
}

async function mountEditor(
  path = '/scenarios/editor',
  options: { loadDetail?: () => Promise<void> } = {},
) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useScenariosStore()
  const validate = vi.spyOn(store, 'validate').mockResolvedValue(undefined)
  const importText = vi.spyOn(store, 'importText').mockResolvedValue(null)
  const loadDetail = vi
    .spyOn(store, 'loadDetail')
    .mockImplementation(options.loadDetail ?? (async () => undefined))
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(ScenarioEditorView, { global: { plugins: [pinia, router] } })
  return { wrapper, store, router, validate, importText, loadDetail }
}

let anchorClick: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:mock-scenario'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ScenarioEditorView（N1-031 场景编辑器）', () => {
  it('默认渲染最小 v2 表单与 JSON 原文；debounce 后自动调用服务端校验', async () => {
    const { wrapper, validate } = await mountEditor()

    expect(wrapper.find('[data-testid="editor-root"]').exists()).toBe(true)
    expect((wrapper.find('[data-testid="editor-slug"]').element as HTMLInputElement).value).toBe(
      'my-first-scene',
    )
    expect((wrapper.find('[data-testid="editor-title"]').element as HTMLInputElement).value).toBe(
      '我的第一幕',
    )
    const json = (wrapper.find('[data-testid="editor-json"]').element as HTMLTextAreaElement).value
    expect(json).toContain('"schema_version": 2')
    expect(json).toContain('"slug": "my-first-scene"')

    expect(validate).not.toHaveBeenCalled()
    await waitForDebounce()
    expect(validate).toHaveBeenCalledTimes(1)
    expect(String(validate.mock.calls[0]?.[0])).toContain('"my-first-scene"')
    wrapper.unmount()
  })

  it('表单改动 → JSON 原文同步；非法 slug 时校验停发并给出中文原因', async () => {
    const { wrapper, validate } = await mountEditor()
    await waitForDebounce()
    validate.mockClear()

    await wrapper.find('[data-testid="editor-title"]').setValue('改过的标题')
    await flushPromises()
    expect(
      (wrapper.find('[data-testid="editor-json"]').element as HTMLTextAreaElement).value,
    ).toContain('改过的标题')

    await wrapper.find('[data-testid="editor-slug"]').setValue('Bad Slug!')
    await waitForDebounce()
    expect(validate).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="editor-validate-status"]').text()).toContain('表单还不完整')
    wrapper.unmount()
  })

  it('服务端校验错误/警告按 JSON 路径展示；校验不通过时保存按钮禁用', async () => {
    const { wrapper, store } = await mountEditor()
    await waitForDebounce()

    store.$patch({
      validateResult: {
        ok: false,
        errors: [{ path: 'rooms[0].description', message: '描述不能为空' }],
        warnings: [{ path: 'flags[0]', message: '声明但未被引用' }],
        summary: OK_SUMMARY,
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="editor-errors"]').text()).toContain(
      'rooms[0].description',
    )
    expect(wrapper.find('[data-testid="editor-warnings"]').text()).toContain('flags[0]')
    expect(wrapper.find('[data-testid="editor-save"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('校验通过后保存：调用 importText（内容哈希幂等键 + json 文件名）并跳详情', async () => {
    const { wrapper, store, router, validate, importText } = await mountEditor()
    validate.mockImplementation(async () => {
      store.validateResult = { ok: true, errors: [], warnings: [], summary: OK_SUMMARY }
      store.validateError = ''
    })
    importText.mockResolvedValue('scn_saved')
    await waitForDebounce()

    const save = wrapper.find('[data-testid="editor-save"]')
    expect(save.attributes('disabled')).toBeUndefined()
    await save.trigger('click')
    await flushPromises()

    expect(importText).toHaveBeenCalledTimes(1)
    const [text, options] = importText.mock.calls[0] as [
      string,
      { filename?: string; actionName?: string },
    ]
    expect(text).toContain('"my-first-scene"')
    expect(options.filename).toBe('my-first-scene.json')
    expect(options.actionName).toMatch(/^scenario-editor-save:[0-9a-f]{8}$/)
    expect(router.currentRoute.value.name).toBe('scenario-detail')
    expect(router.currentRoute.value.params.scenarioId).toBe('scn_saved')
    wrapper.unmount()
  })

  it('v1 场景载入：提示版本、保留 schema_version=1（不擅自升级）', async () => {
    const { wrapper } = await mountEditor('/scenarios/editor?from=scn_old', {
      loadDetail: async () => {
        useScenariosStore().$patch({ detail: V1_DETAIL })
      },
    })
    await flushPromises()
    await flushPromises()

    const select = wrapper.find('[data-testid="editor-schema-version"]')
    expect((select.element as HTMLSelectElement).value).toBe('1')
    expect(wrapper.find('.editor-notice').text()).toContain('v1')
    expect(wrapper.find('.editor-notice').text()).toContain('不会自动升级')
    expect((wrapper.find('[data-testid="editor-title"]').element as HTMLInputElement).value).toBe(
      '小塔',
    )
    wrapper.unmount()
  })

  it('粘贴 JSON（含 v2 flags）→「应用到表单」回填表单并重新校验', async () => {
    const { wrapper, validate } = await mountEditor()
    await waitForDebounce()
    validate.mockClear()

    const pasted = JSON.stringify({
      schema_version: 2,
      slug: 'pasted-scene',
      meta: { title: '粘贴场景' },
      player: { location: 'gate' },
      rooms: [{ key: 'gate', name: '塔门', description: '一扇虚掩的木门。' }],
      flags: [{ key: 'seen_gate', when: { in_room: 'gate' } }],
    })
    await wrapper.find('[data-testid="editor-json"]').setValue(pasted)
    await wrapper.find('[data-testid="editor-apply-json"]').trigger('click')
    await flushPromises()

    expect((wrapper.find('[data-testid="editor-title"]').element as HTMLInputElement).value).toBe(
      '粘贴场景',
    )
    await waitForDebounce()
    expect(validate).toHaveBeenCalledTimes(1)
    expect(String(validate.mock.calls[0]?.[0])).toContain('seen_gate')
    wrapper.unmount()
  })

  it('粘贴非法 JSON → 显示可读错误，表单保持原值', async () => {
    const { wrapper } = await mountEditor()
    await waitForDebounce()

    await wrapper.find('[data-testid="editor-json"]').setValue('{broken')
    await wrapper.find('[data-testid="editor-apply-json"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.editor-json-error').text()).toContain('JSON 语法错误')
    expect((wrapper.find('[data-testid="editor-title"]').element as HTMLInputElement).value).toBe(
      '我的第一幕',
    )
    wrapper.unmount()
  })

  it('下载 JSON：触发浏览器下载（Blob + <a download>）并提示已导出', async () => {
    const { wrapper } = await mountEditor()
    await waitForDebounce()

    await wrapper.find('[data-testid="editor-export"]').trigger('click')
    await flushPromises()

    expect(anchorClick).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.editor-notice').text()).toContain('已导出')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
