<script setup lang="ts">
/**
 * 场景编辑器（N1-031）：表单 ↔ 场景 JSON 双向编辑 + 实时服务端校验 + 保存（幂等导入）。
 *
 * 边界（ADR D-N1-1 / `design/scenario-dsl-v2.md` §4/§5）：
 * - 编辑器**只产出 JSON**：保存仍走 `POST /api/v1/scenarios`（multipart，带 Idempotency-Key），
 *   合法性与白名单永远以服务端 `POST /scenarios/validate` 为准（不旁路、不直写库）；
 * - v1 场景载入后**不擅自升级**：保留 schema_version=1 并提示；作者可显式改成 2 使用 flags/v2 谓词；
 * - 输入内容只做文本插值（不做富文本注入/不执行脚本）；错误/警告带 JSON 路径，`aria-live` 提示；
 * - 自动校验 600ms debounce（`setTimeout`，无 `setInterval`/无自动重试）；保存失败由 Author 手动处理。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import ConditionEditor from '@/components/scenario-editor/ConditionEditor.vue'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { useScenariosStore } from '@/stores/scenarios'
import { downloadTextFile, scenarioJsonFileName } from '@/utils/download'
import {
  SCENARIO_EXIT_DIRECTIONS,
  SCENARIO_EXIT_LABELS,
  SCENARIO_RARITIES,
  SCENARIO_SCHEMA_VERSION_OPTIONS,
  defaultScenarioDraft,
  emptyEndingDraft,
  emptyFlagDraft,
  emptyItemDraft,
  emptyNpcDraft,
  emptyObjectiveDraft,
  emptyRoomDraft,
  emptySuggestionDraft,
  emptyTopicDraft,
  parseDraftFromJsonText,
  parseDefinitionToDraft,
  scenarioEditorSaveAction,
  tryBuildScenarioDefinition,
  type ScenarioDraft,
  type ScenarioSchemaVersion,
} from '@/utils/scenarioEditor'
import { describeScenarioError } from '@/utils/scenarios'

const route = useRoute()
const router = useRouter()
const store = useScenariosStore()

const draft = ref<ScenarioDraft>(defaultScenarioDraft())
const jsonText = ref('')
/** 用户正在 JSON 原文里编辑时，不覆盖其内容（点「应用到表单」后清标记）。 */
const jsonDirty = ref(false)
const jsonError = ref('')
const localError = ref('')
const buildError = ref('')
const versionNotice = ref('')
const loadedFromId = ref('')
const exportNotice = ref('')
const validatedText = ref('')

let validateTimer: ReturnType<typeof setTimeout> | null = null
let validateSeq = 0
const VALIDATE_DEBOUNCE_MS = 600

const built = computed(() => tryBuildScenarioDefinition(draft.value))
const draftJson = computed<string>(() => (built.value.ok ? built.value.text : ''))
const validateResult = computed(() => store.validateResult)
const validateErrors = computed(() => validateResult.value?.errors ?? [])
const validateWarnings = computed(() => validateResult.value?.warnings ?? [])
const saving = computed<boolean>(() => store.importBusy)
/** 保存门槛：本地 build 成功 + 服务端 validate 明确 ok + 校验的就是当前 JSON。 */
const canSave = computed<boolean>(
  () =>
    built.value.ok &&
    !saving.value &&
    validateResult.value?.ok === true &&
    store.validateError === '' &&
    validatedText.value === jsonText.value,
)

const validateStateText = computed<string>(() => {
  if (jsonDirty.value) {
    return 'JSON 原文编辑中：点「应用到表单」把内容同步回表单（或点「用表单刷新 JSON」丢弃改动）。'
  }
  if (buildError.value !== '') {
    return `表单还不完整：${buildError.value}`
  }
  if (store.validateBusy) {
    return '正在校验…'
  }
  if (validateResult.value === null) {
    return `还没有校验结果。先修好表单，服务端会在 ${VALIDATE_DEBOUNCE_MS / 1000} 秒内自动校验（只读、不写库）。`
  }
  if (validateResult.value.ok) {
    return '校验通过：可以保存并开局。'
  }
  return '校验未通过：按下面列出的 JSON 路径修好后会自动重新校验；保存按钮需要校验通过。'
})

/** 草稿变化 → 同步 JSON（用户正在编辑 JSON 时不覆盖）+ debounce 自动校验。 */
watch(
  draft,
  () => {
    if (!jsonDirty.value && built.value.ok) {
      jsonText.value = draftJson.value
    }
    scheduleValidate()
  },
  { deep: true },
)

function scheduleValidate(): void {
  if (validateTimer !== null) {
    clearTimeout(validateTimer)
  }
  validateTimer = setTimeout(() => {
    validateTimer = null
    void runValidate()
  }, VALIDATE_DEBOUNCE_MS)
}

async function runValidate(): Promise<void> {
  if (!jsonDirty.value && !built.value.ok) {
    buildError.value = built.value.ok ? '' : built.value.error
    store.resetValidate()
    validatedText.value = ''
    return
  }
  buildError.value = ''
  const seq = (validateSeq += 1)
  const text = jsonText.value
  await store.validate(text)
  if (seq !== validateSeq) {
    return
  }
  if (store.validateResult?.ok === true && store.validateError === '') {
    validatedText.value = text
  } else {
    validatedText.value = ''
  }
}

function onJsonInput(): void {
  jsonDirty.value = true
  jsonError.value = ''
  scheduleValidate()
}

/** JSON 原文 → 表单草稿（导入粘贴/手工编辑都走这里；v1 提示版本但不升级）。 */
async function onApplyJson(): Promise<void> {
  localError.value = ''
  jsonError.value = ''
  exportNotice.value = ''
  try {
    const next = parseDraftFromJsonText(jsonText.value)
    draft.value = next
    jsonDirty.value = false
    versionNotice.value =
      next.schemaVersion === 1
        ? '载入的是 v1 场景：编辑器不会自动升级版本；如需使用 flags / v2 条件谓词，请把「场景包版本」显式改为 2。'
        : ''
    buildError.value = ''
    validatedText.value = ''
    await nextTick()
    scheduleValidate()
  } catch (error) {
    jsonError.value = error instanceof Error ? error.message : String(error)
  }
}

/** 丢弃 JSON 原文改动，用当前表单重新生成 JSON。 */
function onRefreshJson(): void {
  if (!built.value.ok) {
    localError.value = built.value.error
    return
  }
  jsonText.value = built.value.text
  jsonDirty.value = false
  jsonError.value = ''
  buildError.value = ''
  validatedText.value = ''
  scheduleValidate()
}

function onVersionChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value === '2' ? 2 : 1
  draft.value.schemaVersion = value as ScenarioSchemaVersion
  versionNotice.value =
    value === 1
      ? 'schema_version=1 只支持 v1 字段/谓词；flags 与 v2 条件会被服务端拒绝（不静默升级）。'
      : ''
}

/** 下载当前草稿 JSON（纯前端 Blob 下载；不调用导出端点）。 */
function onDownload(): void {
  localError.value = ''
  exportNotice.value = ''
  const text = built.value.ok ? built.value.text : jsonText.value
  if (text.trim() === '') {
    localError.value = '当前还没有可下载的 JSON。'
    return
  }
  if (!downloadTextFile(scenarioJsonFileName(draft.value.slug), text)) {
    localError.value = '当前浏览器不支持直接下载：请复制「JSON 原文」面板里的内容自行保存。'
    return
  }
  exportNotice.value = `已导出 ${scenarioJsonFileName(draft.value.slug)}。`
}

/** 保存：服务端校验通过后按 JSON 原文导入（幂等键按内容哈希；同内容手动重试复用）。 */
async function onSave(): Promise<void> {
  localError.value = ''
  if (!canSave.value) {
    localError.value = '请先等自动校验通过（表单不完整/有错误时保存会被禁用）。'
    return
  }
  const id = await store.importText(jsonText.value, {
    filename: scenarioJsonFileName(draft.value.slug),
    actionName: scenarioEditorSaveAction(jsonText.value),
  })
  if (id !== null) {
    await router.push({ name: 'scenario-detail', params: { scenarioId: id } })
  }
}

function addRoom(): void {
  draft.value.rooms.push(emptyRoomDraft(draft.value.rooms.length + 1))
}
function removeRoom(index: number): void {
  if (draft.value.rooms.length <= 1) {
    localError.value = '至少保留 1 个房间。'
    return
  }
  draft.value.rooms.splice(index, 1)
}
function addItem(): void {
  draft.value.items.push(emptyItemDraft(draft.value.items.length + 1))
}
function removeItem(index: number): void {
  draft.value.items.splice(index, 1)
}
function addNpc(): void {
  draft.value.npcs.push(emptyNpcDraft(draft.value.npcs.length + 1))
}
function removeNpc(index: number): void {
  draft.value.npcs.splice(index, 1)
}
function addTopic(index: number): void {
  draft.value.npcs[index]?.topics.push(emptyTopicDraft())
}
function removeTopic(npcIndex: number, topicIndex: number): void {
  draft.value.npcs[npcIndex]?.topics.splice(topicIndex, 1)
}
function addFlag(): void {
  draft.value.flags.push(emptyFlagDraft())
}
function removeFlag(index: number): void {
  draft.value.flags.splice(index, 1)
}
function addObjective(): void {
  draft.value.goal.objectives.push(emptyObjectiveDraft(draft.value.goal.objectives.length + 1))
}
function removeObjective(index: number): void {
  draft.value.goal.objectives.splice(index, 1)
}
function addEnding(): void {
  draft.value.endings.push(emptyEndingDraft(draft.value.endings.length + 1))
}
function removeEnding(index: number): void {
  draft.value.endings.splice(index, 1)
}
function addSuggestion(): void {
  draft.value.suggestions.push(emptySuggestionDraft())
}
function removeSuggestion(index: number): void {
  draft.value.suggestions.splice(index, 1)
}

/** 载入 `?from=<scenario_id>`：把已导入场景的定义回填为草稿（N1-032 分享回填/编辑入口）。 */
async function loadFromDetail(scenarioId: string): Promise<void> {
  localError.value = ''
  try {
    await store.loadDetail(scenarioId)
  } catch (error) {
    localError.value = describeScenarioError(error).message
    return
  }
  const definition = store.detail?.definition
  if (definition === undefined) {
    localError.value = '没有读到这个场景的定义，无法载入编辑。'
    return
  }
  try {
    const next = parseDefinitionToDraft(definition)
    draft.value = next
    loadedFromId.value = scenarioId
    jsonText.value = JSON.stringify(definition, null, 2)
    jsonDirty.value = false
    versionNotice.value =
      next.schemaVersion === 1
        ? '载入的是 v1 场景：编辑器不会自动升级版本；如需使用 flags / v2 条件谓词，请把「场景包版本」显式改为 2。'
        : ''
    await nextTick()
    scheduleValidate()
  } catch (error) {
    localError.value = error instanceof Error ? error.message : String(error)
  }
}

onMounted(async () => {
  jsonText.value = built.value.ok ? built.value.text : ''
  const from = route.query.from
  if (typeof from === 'string' && from !== '') {
    await loadFromDetail(from)
    return
  }
  scheduleValidate()
})

onBeforeUnmount(() => {
  if (validateTimer !== null) {
    clearTimeout(validateTimer)
    validateTimer = null
  }
  // 离开编辑器时清掉校验/导入的临时状态，避免下一次进入看到上一次的残留提示。
  store.resetValidate()
  store.resetImport()
})
</script>

<template>
  <section class="scenario-editor-view" data-testid="editor-root">
    <header class="game-head">
      <h2>场景编辑器</h2>
      <p class="game-meta">
        在这里写一份场景，保存到场景库后就能开局。编辑器只产出 JSON；保存前会实时调用服务端
        <code>/scenarios/validate</code> 校验（只读、不写库），通过后才能保存。
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'scenarios' }">
        ← 返回场景库
      </RouterLink>
      <RouterLink v-if="loadedFromId !== ''" class="btn btn-small btn-secondary" :to="{ name: 'scenario-detail', params: { scenarioId: loadedFromId } }">
        查看原场景详情
      </RouterLink>
      <button class="btn btn-small btn-secondary" type="button" data-testid="editor-export" @click="onDownload">
        下载 JSON
      </button>
      <button
        class="btn btn-small"
        type="button"
        data-testid="editor-save"
        :disabled="!canSave"
        @click="onSave"
      >
        {{ saving ? '保存中…' : '保存到场景库' }}
      </button>
      <span v-if="loadedFromId === ''" class="muted">新建场景：保存成功后会进入详情页，可直接开局。</span>
      <span v-else class="muted">编辑已有场景：保存会生成一份新场景（同一 slug 不覆盖旧存档）。</span>
    </div>

    <ErrorBanner :message="localError" />
    <ErrorBanner :title="store.importError?.title" :message="store.importError?.message ?? ''" :detail="store.importError?.detail" />

    <p v-if="versionNotice !== ''" class="panel editor-notice" role="status">{{ versionNotice }}</p>
    <p v-if="exportNotice !== ''" class="panel editor-notice" role="status">{{ exportNotice }}</p>

    <section class="panel editor-validate" aria-labelledby="editor-validate-title">
      <h3 id="editor-validate-title" class="panel-title">实时校验（服务端）</h3>
      <p class="muted" data-testid="editor-validate-status" role="status" aria-live="polite">
        {{ validateStateText }}
      </p>
      <template v-if="validateResult !== null">
        <section v-if="validateResult.ok" class="editor-validate-ok" aria-label="校验通过摘要">
          <p class="muted">
            房间 {{ validateResult.summary.rooms }} · 物品 {{ validateResult.summary.items }} · 角色
            {{ validateResult.summary.npcs }} · 目标 {{ validateResult.summary.objectives }} · 结局
            {{ validateResult.summary.endings }} · 建议 {{ validateResult.summary.suggestions }}
          </p>
        </section>
        <section v-else aria-label="校验错误">
          <h4 class="panel-subtitle">错误（{{ validateErrors.length }}）</h4>
          <ul class="plain-list scenario-issue-list" data-testid="editor-errors">
            <li v-for="(item, index) in validateErrors" :key="`${item.path}-${index}`">
              {{ item.path === '' ? item.message : `${item.path}：${item.message}` }}
            </li>
          </ul>
        </section>
      </template>
      <section v-if="validateWarnings.length > 0" aria-label="校验提示">
        <h4 class="panel-subtitle">提示（不阻断，{{ validateWarnings.length }}）</h4>
        <ul class="plain-list scenario-issue-list" data-testid="editor-warnings">
          <li v-for="(item, index) in validateWarnings" :key="`${item.path}-${index}`">
            {{ item.path === '' ? item.message : `${item.path}：${item.message}` }}
          </li>
        </ul>
      </section>
    </section>

    <!-- 基本信息 -->
    <section class="panel editor-section" aria-labelledby="editor-basic-title">
      <h3 id="editor-basic-title" class="panel-title">基本信息</h3>
      <div class="editor-grid">
        <label class="field">
          <span>场景包版本 <code>schema_version</code></span>
          <select class="input" data-testid="editor-schema-version" :value="draft.schemaVersion" @change="onVersionChange">
            <option v-for="version in SCENARIO_SCHEMA_VERSION_OPTIONS" :key="version" :value="version">
              v{{ version }}{{ version === 2 ? '（支持 flags / v2 条件）' : '（v1 冻结，不支持 v2 特性）' }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>slug（小写字母/数字/连字符，2–32）</span>
          <input v-model="draft.slug" class="input" type="text" maxlength="32" data-testid="editor-slug" />
        </label>
        <label class="field">
          <span>标题（1–48 字）</span>
          <input v-model="draft.meta.title" class="input" type="text" maxlength="48" data-testid="editor-title" />
        </label>
        <label class="field">
          <span>作者（可选，≤32）</span>
          <input v-model="draft.meta.author" class="input" type="text" maxlength="32" />
        </label>
        <label class="field editor-span-2">
          <span>简介（可选，≤200）</span>
          <input v-model="draft.meta.description" class="input" type="text" maxlength="200" />
        </label>
        <label class="field editor-span-2">
          <span>标签（可选，最多 8 个；逗号分隔）</span>
          <input v-model="draft.meta.tagsText" class="input" type="text" placeholder="例如：末日, 解谜" />
        </label>
      </div>
    </section>

    <!-- 玩家 -->
    <section class="panel editor-section" aria-labelledby="editor-player-title">
      <h3 id="editor-player-title" class="panel-title">玩家</h3>
      <div class="editor-grid">
        <label class="field">
          <span>名字（默认「旅人」）</span>
          <input v-model="draft.player.name" class="input" type="text" maxlength="24" />
        </label>
        <label class="field">
          <span>初始房间 key</span>
          <input v-model="draft.player.location" class="input" type="text" data-testid="editor-player-location" />
        </label>
        <label class="field">
          <span>HP</span>
          <input v-model="draft.player.hp" class="input" type="number" inputmode="numeric" />
        </label>
        <label class="field">
          <span>HP 上限</span>
          <input v-model="draft.player.maxHp" class="input" type="number" inputmode="numeric" />
        </label>
        <label class="field editor-span-2">
          <span>初始行囊（物品 key，逗号分隔）</span>
          <input v-model="draft.player.inventoryText" class="input" type="text" />
        </label>
      </div>
    </section>

    <!-- 房间 -->
    <section class="panel editor-section" aria-labelledby="editor-rooms-title">
      <div class="editor-section-head">
        <h3 id="editor-rooms-title" class="panel-title">房间（{{ draft.rooms.length }}）</h3>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-room" @click="addRoom">添加房间</button>
      </div>
      <div v-for="(room, index) in draft.rooms" :key="`room-${index}`" class="editor-card">
        <div class="editor-card-head">
          <h4 class="panel-subtitle">房间 {{ index + 1 }}</h4>
          <button class="btn btn-small btn-secondary" type="button" @click="removeRoom(index)">删除</button>
        </div>
        <div class="editor-grid">
          <label class="field"><span>key</span><input v-model="room.key" class="input" type="text" /></label>
          <label class="field"><span>名称</span><input v-model="room.name" class="input" type="text" maxlength="32" /></label>
          <label class="field editor-span-2"><span>描述（1–500 字）</span><textarea v-model="room.description" class="input" rows="2" maxlength="500"></textarea></label>
          <label class="field"><span>初始物品（key，逗号分隔）</span><input v-model="room.itemsText" class="input" type="text" /></label>
          <label class="field"><span>初始 NPC（key，逗号分隔）</span><input v-model="room.npcsText" class="input" type="text" /></label>
          <label class="field editor-span-2"><span>环境标签（可选，逗号分隔）</span><input v-model="room.ambientTagsText" class="input" type="text" /></label>
          <div class="field editor-span-2">
            <span>出口（方向 → 房间 key）</span>
            <div class="editor-exits">
              <label v-for="direction in SCENARIO_EXIT_DIRECTIONS" :key="direction" class="editor-exit">
                <span>{{ SCENARIO_EXIT_LABELS[direction] }}（{{ direction }}）</span>
                <input v-model="room.exits[direction]" class="input" type="text" />
              </label>
            </div>
          </div>
          <label class="field"><span>进入扣血（可选，负数，如 -2）</span><input v-model="room.hpDelta" class="input" type="number" inputmode="numeric" /></label>
          <label class="field"><span>进入提示（可选，≤120 字）</span><input v-model="room.hint" class="input" type="text" maxlength="120" /></label>
        </div>
      </div>
    </section>

    <!-- 物品 -->
    <section class="panel editor-section" aria-labelledby="editor-items-title">
      <div class="editor-section-head">
        <h3 id="editor-items-title" class="panel-title">物品（{{ draft.items.length }}）</h3>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-item" @click="addItem">添加物品</button>
      </div>
      <div v-for="(item, index) in draft.items" :key="`item-${index}`" class="editor-card">
        <div class="editor-card-head">
          <h4 class="panel-subtitle">物品 {{ index + 1 }}</h4>
          <button class="btn btn-small btn-secondary" type="button" @click="removeItem(index)">删除</button>
        </div>
        <div class="editor-grid">
          <label class="field"><span>key</span><input v-model="item.key" class="input" type="text" /></label>
          <label class="field"><span>名称</span><input v-model="item.name" class="input" type="text" maxlength="32" /></label>
          <label class="field editor-span-2"><span>描述（可选，≤500）</span><textarea v-model="item.description" class="input" rows="2" maxlength="500"></textarea></label>
          <label class="field"><span>标签（如 quest/weapon，逗号分隔）</span><input v-model="item.tagsText" class="input" type="text" /></label>
          <label class="field">
            <span>稀有度（可选）</span>
            <select v-model="item.rarity" class="input">
              <option value="">（默认）</option>
              <option v-for="rarity in SCENARIO_RARITIES" :key="rarity" :value="rarity">{{ rarity }}</option>
            </select>
          </label>
          <label class="field"><span>治疗量 heal（可选）</span><input v-model="item.heal" class="input" type="number" inputmode="numeric" /></label>
          <label class="field"><span>伤害 damage（可选）</span><input v-model="item.damage" class="input" type="number" inputmode="numeric" /></label>
        </div>
      </div>
    </section>

    <!-- NPC -->
    <section class="panel editor-section" aria-labelledby="editor-npcs-title">
      <div class="editor-section-head">
        <h3 id="editor-npcs-title" class="panel-title">角色 / NPC（{{ draft.npcs.length }}）</h3>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-npc" @click="addNpc">添加角色</button>
      </div>
      <div v-for="(npc, index) in draft.npcs" :key="`npc-${index}`" class="editor-card">
        <div class="editor-card-head">
          <h4 class="panel-subtitle">角色 {{ index + 1 }}</h4>
          <button class="btn btn-small btn-secondary" type="button" @click="removeNpc(index)">删除</button>
        </div>
        <div class="editor-grid">
          <label class="field"><span>key</span><input v-model="npc.key" class="input" type="text" /></label>
          <label class="field"><span>名称</span><input v-model="npc.name" class="input" type="text" maxlength="24" /></label>
          <label class="field"><span>所在房间 key</span><input v-model="npc.room" class="input" type="text" /></label>
          <label class="field"><span>细看描述（可选，≤500）</span><input v-model="npc.description" class="input" type="text" maxlength="500" /></label>
          <label class="field"><span>默认回话（可选，≤500）</span><input v-model="npc.dialogueDefault" class="input" type="text" maxlength="500" /></label>
          <label class="field"><span>已知情回话（可选）</span><input v-model="npc.briefedReply" class="input" type="text" maxlength="500" /></label>
        </div>
        <div class="editor-card">
          <div class="editor-section-head">
            <h4 class="panel-subtitle">对话主题（{{ npc.topics.length }}）</h4>
            <button class="btn btn-small btn-secondary" type="button" @click="addTopic(index)">添加主题</button>
          </div>
          <div v-for="(topic, topicIndex) in npc.topics" :key="`topic-${topicIndex}`" class="editor-card">
            <div class="editor-grid">
              <label class="field"><span>关键词（逗号分隔，1–8 个）</span><input v-model="topic.keywordsText" class="input" type="text" /></label>
              <label class="field"><span>回话（1–500 字）</span><input v-model="topic.reply" class="input" type="text" maxlength="500" /></label>
              <label class="field"><span>对话态（可选，如 briefed）</span><input v-model="topic.dialogueState" class="input" type="text" maxlength="32" /></label>
              <div class="field editor-inline-actions">
                <button class="btn btn-small btn-secondary" type="button" :aria-label="`删除第 ${topicIndex + 1} 个对话主题`" @click="removeTopic(index, topicIndex)">
                  删除主题
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- v2 剧情开关 -->
    <section class="panel editor-section" aria-labelledby="editor-flags-title">
      <div class="editor-section-head">
        <h3 id="editor-flags-title" class="panel-title">剧情开关 flags（v2，{{ draft.flags.length }}）</h3>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-flag" :disabled="draft.schemaVersion !== 2" @click="addFlag">
          添加开关
        </button>
      </div>
      <p v-if="draft.schemaVersion !== 2" class="field-hint muted">
        flags 是 v2 字段：当前版本为 v1（不自动升级）。把上方「场景包版本」改为 2 后才能添加。
      </p>
      <div v-for="(flag, index) in draft.flags" :key="`flag-${index}`" class="editor-card">
        <div class="editor-card-head">
          <h4 class="panel-subtitle">开关 {{ index + 1 }}</h4>
          <button class="btn btn-small btn-secondary" type="button" @click="removeFlag(index)">删除</button>
        </div>
        <label class="field">
          <span>flag key（文件内唯一，≤16 个）</span>
          <input v-model="flag.key" class="input" type="text" data-testid="editor-flag-key" />
        </label>
        <ConditionEditor
          :model-value="flag.when"
          label="触发条件（when）"
          @update:model-value="flag.when = $event"
        />
      </div>
    </section>

    <!-- 目标 -->
    <section class="panel editor-section" aria-labelledby="editor-goal-title">
      <h3 id="editor-goal-title" class="panel-title">目标</h3>
      <label class="editor-checkbox">
        <input v-model="draft.goal.enabled" type="checkbox" />
        <span>显示目标面板（启用后至少 1 条子目标）</span>
      </label>
      <div v-if="draft.goal.enabled" class="editor-card">
        <div class="editor-grid">
          <label class="field"><span>目标标题（1–48 字）</span><input v-model="draft.goal.title" class="input" type="text" maxlength="48" /></label>
          <label class="field"><span>目标描述（可选，≤300）</span><input v-model="draft.goal.description" class="input" type="text" maxlength="300" /></label>
        </div>
        <div class="editor-section-head">
          <h4 class="panel-subtitle">子目标（{{ draft.goal.objectives.length }}）</h4>
          <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-objective" @click="addObjective">添加子目标</button>
        </div>
        <div v-for="(objective, index) in draft.goal.objectives" :key="`objective-${index}`" class="editor-card">
          <div class="editor-card-head">
            <h4 class="panel-subtitle">子目标 {{ index + 1 }}</h4>
            <button class="btn btn-small btn-secondary" type="button" @click="removeObjective(index)">删除</button>
          </div>
          <div class="editor-grid">
            <label class="field"><span>key</span><input v-model="objective.key" class="input" type="text" /></label>
            <label class="field"><span>文案（1–120 字）</span><input v-model="objective.text" class="input" type="text" maxlength="120" /></label>
          </div>
          <ConditionEditor :model-value="objective.when" label="完成条件（when）" @update:model-value="objective.when = $event" />
        </div>
      </div>
    </section>

    <!-- 结局 -->
    <section class="panel editor-section" aria-labelledby="editor-endings-title">
      <div class="editor-section-head">
        <h3 id="editor-endings-title" class="panel-title">结局（{{ draft.endings.length }}）</h3>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-ending" @click="addEnding">添加结局</button>
      </div>
      <p class="field-hint muted">
        按 priority 降序 → 声明顺序取第一个满足项；不写结局时按兜底规则（目标全完成=胜利、HP≤0=倒下）。
      </p>
      <div v-for="(ending, index) in draft.endings" :key="`ending-${index}`" class="editor-card">
        <div class="editor-card-head">
          <h4 class="panel-subtitle">结局 {{ index + 1 }}</h4>
          <button class="btn btn-small btn-secondary" type="button" @click="removeEnding(index)">删除</button>
        </div>
        <div class="editor-grid">
          <label class="field"><span>key</span><input v-model="ending.key" class="input" type="text" /></label>
          <label class="field">
            <span>kind</span>
            <select v-model="ending.kind" class="input">
              <option value="win">win（胜利）</option>
              <option value="lose">lose（失败）</option>
            </select>
          </label>
          <label class="field"><span>标题（1–48 字）</span><input v-model="ending.title" class="input" type="text" maxlength="48" /></label>
          <label class="field"><span>优先级 priority（-100..100）</span><input v-model="ending.priority" class="input" type="number" inputmode="numeric" /></label>
          <label class="field editor-span-2"><span>正文（1–800 字）</span><textarea v-model="ending.text" class="input" rows="2" maxlength="800"></textarea></label>
        </div>
        <ConditionEditor :model-value="ending.when" label="触发条件（when）" @update:model-value="ending.when = $event" />
      </div>
    </section>

    <!-- 建议动作 -->
    <section class="panel editor-section" aria-labelledby="editor-suggestions-title">
      <div class="editor-section-head">
        <h3 id="editor-suggestions-title" class="panel-title">建议动作（{{ draft.suggestions.length }}）</h3>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-add-suggestion" @click="addSuggestion">添加建议</button>
      </div>
      <div v-for="(suggestion, index) in draft.suggestions" :key="`suggestion-${index}`" class="editor-card">
        <div class="editor-card-head">
          <h4 class="panel-subtitle">建议 {{ index + 1 }}</h4>
          <button class="btn btn-small btn-secondary" type="button" @click="removeSuggestion(index)">删除</button>
        </div>
        <label class="field"><span>文案（1–120 字）</span><input v-model="suggestion.text" class="input" type="text" maxlength="120" /></label>
        <ConditionEditor :model-value="suggestion.when" label="出现条件（when，可选）" @update:model-value="suggestion.when = $event" />
      </div>
    </section>

    <!-- JSON 原文 / 导入粘贴 -->
    <details class="panel editor-json-panel" :open="jsonError !== ''">
      <summary>JSON 原文 / 导入粘贴（与表单双向同步）</summary>
      <div class="game-tools">
        <button class="btn btn-small" type="button" data-testid="editor-apply-json" @click="onApplyJson">应用到表单</button>
        <button class="btn btn-small btn-secondary" type="button" data-testid="editor-refresh-json" @click="onRefreshJson">
          用表单刷新 JSON
        </button>
        <button class="btn btn-small btn-secondary" type="button" @click="onDownload">下载 JSON</button>
      </div>
      <p v-if="jsonError !== ''" class="editor-json-error" role="alert">{{ jsonError }}</p>
      <label class="field">
        <span>JSON 原文（可直接粘贴从场景详情导出的 &lt;slug&gt;.json）</span>
        <textarea
          v-model="jsonText"
          class="input card-json-input"
          rows="18"
          data-testid="editor-json"
          spellcheck="false"
          @input="onJsonInput"
        ></textarea>
      </label>
      <p class="field-hint muted">
        粘贴后点「应用到表单」回填表单；表单改动会自动刷新这里的 JSON。保存时提交的就是这段 JSON
        原文（服务端校验/白名单是唯一权威）。
      </p>
    </details>

    <p class="muted">
      编辑器不会执行你写的 JSON（无脚本/表达式）；保存成功后会进入详情页，可按此场景开局。
    </p>
  </section>
</template>
