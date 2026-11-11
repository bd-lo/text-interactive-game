<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { useScenariosStore } from '@/stores/scenarios'
import {
  checkScenarioFileName,
  checkScenarioFileSize,
  formatScenarioIssue,
  formatScenarioStats,
  SCENARIO_MAX_BYTES,
} from '@/utils/scenarios'

const router = useRouter()
const store = useScenariosStore()

const jsonText = ref('')
const fileName = ref('')
const localError = ref('')
const dragging = ref(false)

const validateResult = computed(() => store.validateResult)
const validateErrors = computed(() => validateResult.value?.errors ?? [])
const validateWarnings = computed(() => validateResult.value?.warnings ?? [])
const importError = computed(() => store.importError)
const importWarnings = computed(() => store.importWarnings)

/** 校验状态一句化（导入按钮的启用条件由 store.canImport 控制）。 */
const validateStateText = computed<string>(() => {
  if (store.validateBusy) {
    return '正在校验…'
  }
  if (validateResult.value === null) {
    return '还没有校验。先点「校验（只看不存）」：不通过会逐条列出 JSON 路径与原因。'
  }
  if (validateResult.value.ok) {
    return '校验通过：格式合法，可以导入。'
  }
  return '校验未通过：按下面列出的 JSON 路径修好后，重新校验通过才能导入。'
})

/** 读取文件：文件名后缀/大小预检 → 文本读入（UTF-8 检查在提交校验时统一做）。 */
async function readFile(file: File): Promise<void> {
  localError.value = ''
  const nameError = checkScenarioFileName(file.name)
  if (nameError !== '') {
    localError.value = nameError
    return
  }
  const sizeError = checkScenarioFileSize(file.size)
  if (sizeError !== '') {
    localError.value = sizeError
    return
  }
  fileName.value = file.name
  jsonText.value = await file.text()
}

async function onFileChange(event: Event): Promise<void> {
  localError.value = ''
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file !== undefined) {
    await readFile(file)
  }
}

async function onDrop(event: DragEvent): Promise<void> {
  dragging.value = false
  localError.value = ''
  const file = event.dataTransfer?.files?.[0]
  if (file === undefined) {
    localError.value = '没有拖进文件：请拖一份 .json 场景文件，或点上面的「选择 JSON 文件」。'
    return
  }
  await readFile(file)
}

async function onValidate(): Promise<void> {
  localError.value = ''
  await store.validate(jsonText.value)
}

async function onImport(): Promise<void> {
  localError.value = ''
  if (validateResult.value === null || !validateResult.value.ok) {
    localError.value = '请先点「校验（只看不存）」并等它通过，再确认导入。'
    return
  }
  const scenarioId = await store.importText(jsonText.value, {
    filename: fileName.value === '' ? undefined : fileName.value,
  })
  if (scenarioId !== null) {
    await router.push({ name: 'scenario-detail', params: { scenarioId } })
  }
}

function onClear(): void {
  jsonText.value = ''
  fileName.value = ''
  localError.value = ''
  store.resetImport()
  store.resetValidate()
}

// 编辑内容后旧校验结果立即作废：避免「校验的是 A、导入的是 B」。
watch(jsonText, () => {
  if (store.validateResult !== null || store.validateError !== '') {
    store.resetValidate()
  }
  localError.value = ''
})
</script>

<template>
  <section class="scenario-import-view">
    <header class="game-head">
      <h2>导入场景</h2>
      <p class="game-meta">
        选择或粘贴一份场景 JSON（单个文件不超过 256KB / {{ SCENARIO_MAX_BYTES }} 字节，UTF-8 编码）。
        先校验（只读、不写库），通过后再确认导入；导入后立即可以开局。
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'scenarios' }">
        ← 返回场景库
      </RouterLink>
      <span class="muted">场景格式见「场景设计」：schema_version / meta / rooms / items / npcs / goal / endings。</span>
    </div>

    <ErrorBanner :message="localError" />

    <form class="panel scenario-import-form" @submit.prevent="onValidate">
      <label class="field">
        <span>选择 JSON 文件</span>
        <input type="file" accept=".json,application/json" @change="onFileChange" />
        <span class="field-hint muted">也可以把 .json 文件直接拖进下面的文本框区域。</span>
      </label>
      <p v-if="fileName !== ''" class="muted">已选择：{{ fileName }}</p>
      <div
        class="world-book-dropzone scenario-dropzone"
        :class="{ 'world-book-dropzone-active': dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <label class="field">
          <span>或粘贴场景 JSON</span>
          <textarea
            v-model="jsonText"
            class="input card-json-input"
            rows="16"
            placeholder='粘贴场景 JSON，例如 {"schema_version":1,"slug":"tiny-tower","meta":{"title":"小塔"},"player":{"location":"gate"},"rooms":[{"key":"gate","name":"塔门","description":"一扇虚掩的木门。"}]}'
          ></textarea>
        </label>
      </div>
      <p class="muted" role="status">{{ validateStateText }}</p>
      <div class="game-tools">
        <button class="btn" type="submit" :disabled="store.validateBusy">
          {{ store.validateBusy ? '校验中…' : '校验（只看不存）' }}
        </button>
        <button class="btn" type="button" :disabled="!store.canImport" @click="onImport">
          {{ store.importBusy ? '导入中…' : '确认导入并保存' }}
        </button>
        <button class="btn btn-secondary" type="button" @click="onClear">清空</button>
      </div>
    </form>

    <ErrorBanner :message="store.validateError" />
    <template v-if="validateResult !== null">
      <section v-if="validateResult.ok" class="panel scenario-preview-panel">
        <h3 class="panel-title">校验通过</h3>
        <dl class="status-grid">
          <dt>房间</dt>
          <dd>{{ validateResult.summary.rooms }} 个</dd>
          <dt>物品</dt>
          <dd>{{ validateResult.summary.items }} 件</dd>
          <dt>角色（NPC）</dt>
          <dd>{{ validateResult.summary.npcs }} 个</dd>
          <dt>目标</dt>
          <dd>{{ validateResult.summary.objectives }} 个</dd>
          <dt>结局</dt>
          <dd>{{ validateResult.summary.endings }} 个</dd>
          <dt>建议动作</dt>
          <dd>{{ validateResult.summary.suggestions }} 条</dd>
        </dl>
        <p class="muted">共 {{ formatScenarioStats(validateResult.summary) }}。</p>
      </section>
      <section v-else class="panel scenario-preview-panel">
        <h3 class="panel-title">校验未通过</h3>
        <p class="muted">
          共 {{ validateErrors.length }} 处问题；每行前面的路径指出 JSON 里的位置（例如
          rooms[2].exits.east 指第 3 个房间的 east 出口）。
        </p>
        <ul class="plain-list scenario-issue-list">
          <li v-for="(item, index) in validateErrors" :key="`${item.path}-${index}`">
            {{ formatScenarioIssue(item) }}
          </li>
        </ul>
      </section>
      <section v-if="validateWarnings.length > 0" class="panel scenario-preview-panel">
        <h3 class="panel-title">提示（不阻断导入）</h3>
        <ul class="plain-list scenario-issue-list">
          <li v-for="(item, index) in validateWarnings" :key="`${item.path}-${index}`">
            {{ formatScenarioIssue(item) }}
          </li>
        </ul>
      </section>
    </template>

    <ErrorBanner
      :title="importError?.title"
      :message="importError?.message ?? ''"
      :detail="importError?.detail"
    />
    <section v-if="importWarnings.length > 0" class="panel">
      <h3 class="panel-title">导入提示</h3>
      <ul class="plain-list scenario-issue-list">
        <li v-for="(item, index) in importWarnings" :key="`${item.path}-${index}`">
          {{ formatScenarioIssue(item) }}
        </li>
      </ul>
    </section>

    <p class="muted">
      导入会生成一个新的内部场景 ID（同一 slug 重复导入不会覆盖已开局的存档）。
      关闭 DeepSeek API Key 时用离线模板叙事，配置后由 LLM 叙事。
    </p>
  </section>
</template>
