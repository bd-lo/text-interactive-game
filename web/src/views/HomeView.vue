<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { getLlmSettings, listGames, listScenarios, listWorldBooks } from '@/api/client'
import type {
  GameListItem,
  LlmSettingsStatus,
  Scenario,
  ScenarioSummary,
  WorldBookSummary,
} from '@/api/types'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { vReveal } from '@/directives/reveal'
import { useGameStore } from '@/stores/game'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'
import { labelGameStatus, labelScenario } from '@/utils/labels'
import { dismissLlmBanner, isLlmBannerDismissed, shouldShowLlmBanner } from '@/utils/llm'
import {
  describeRecentGame,
  formatRelativeTime,
  loadRecentGames,
  type RecentGameRecord,
} from '@/utils/recentGames'
import { formatScenarioStats } from '@/utils/scenarios'

const router = useRouter()
const store = useGameStore()

const SAVE_LIMIT = 10

/** 当前选择：内置场景 ID（m2/m3/m4/story）或已导入场景的 `scenario_id`（`scn_...`）。 */
const scenarioChoice = ref<string>('story')
/** `input type=number` 在 Vue 里会自动 number 化，空值仍是字符串，这里两种都接受。 */
const seedText = ref<string | number>('')
const manualId = ref('')

const saves = ref<GameListItem[]>([])
const savesLoading = ref(false)
const savesFailed = ref(false)
const recentGames = ref<RecentGameRecord[]>([])
const createError = ref<PlayerErrorInfo | null>(null)

/** 世界书下拉（H4）：开新局可选绑定一本；读取失败不阻塞开局。 */
const worldBooks = ref<WorldBookSummary[]>([])
const worldBookId = ref('')
const worldBooksFailed = ref(false)
const WORLD_BOOK_OPTION_LIMIT = 50

/** 场景下拉（玩家自上传场景）：内置 4 个 + 已导入场景；后端不可用时只显示内置。 */
const importedScenarios = ref<ScenarioSummary[]>([])
const scenariosFailed = ref(false)
const SCENARIO_OPTION_LIMIT = 50

/** 首屏「未配置大模型」横幅：状态读取失败不显示（不打扰）；关闭后记住（localStorage）。 */
const llmStatus = ref<LlmSettingsStatus | null>(null)
const llmBannerDismissed = ref(isLlmBannerDismissed())
const showLlmBanner = computed<boolean>(() =>
  shouldShowLlmBanner(llmStatus.value, llmBannerDismissed.value),
)

const scenarioOptions: Array<{ value: Scenario; label: string; hint: string }> = [
  { value: 'story', label: '余烬哨站（剧情）', hint: '有目标与结局，推荐第一次玩' },
  { value: 'm2', label: '废弃哨站（入门）', hint: '一小段探索' },
  { value: 'm3', label: '拾荒者营地（多角色）', hint: '会碰到别的角色' },
  { value: 'm4', label: '工坊（制造）', hint: '可以制作东西' },
]

/** 当前选中项：已导入场景（选了自定义场景时非 null）。 */
const selectedCustomScenario = computed<ScenarioSummary | null>(
  () =>
    importedScenarios.value.find((item) => item.scenario_id === scenarioChoice.value) ?? null,
)
/** 内置场景的提示文案。 */
const builtinScenarioHint = computed<string>(
  () => scenarioOptions.find((option) => option.value === scenarioChoice.value)?.hint ?? '',
)

/** 读取服务端存档列表；失败时退回本机最近对局（localStorage），不影响新建对局。 */
async function loadSaves(): Promise<void> {
  savesLoading.value = true
  savesFailed.value = false
  try {
    const response = await listGames({ limit: SAVE_LIMIT })
    saves.value = response.games
  } catch {
    savesFailed.value = true
    saves.value = []
    recentGames.value = loadRecentGames()
  } finally {
    savesLoading.value = false
  }
}

/** 读取可选世界书列表（失败时只提示，不影响开局）。 */
async function loadWorldBooks(): Promise<void> {
  worldBooksFailed.value = false
  try {
    const response = await listWorldBooks({ limit: WORLD_BOOK_OPTION_LIMIT })
    worldBooks.value = response.world_books
  } catch {
    worldBooks.value = []
    worldBooksFailed.value = true
  }
}

/** 读取已导入场景（失败时下拉只显示内置场景，不影响开局）。 */
async function loadImportedScenarios(): Promise<void> {
  scenariosFailed.value = false
  try {
    const response = await listScenarios({ limit: SCENARIO_OPTION_LIMIT })
    importedScenarios.value = response.scenarios
  } catch {
    importedScenarios.value = []
    scenariosFailed.value = true
  }
}

/** 读取 LLM 配置状态（失败不显示横幅；不阻塞开局）。 */
async function loadLlmStatus(): Promise<void> {
  try {
    llmStatus.value = await getLlmSettings()
  } catch {
    llmStatus.value = null
  }
}

/** 关闭「未配置」横幅并记住（避免每次回首页都打扰；可在设置页重新配置）。 */
function onDismissLlmBanner(): void {
  llmBannerDismissed.value = true
  dismissLlmBanner()
}

/** 解析随机种子：留空 → undefined（服务端默认）；非法 → 返回错误文案。 */
function parseSeed(): { seed?: number; error?: string } {
  const raw = typeof seedText.value === 'number' ? String(seedText.value) : seedText.value.trim()
  if (raw === '') {
    return {}
  }
  const value = Number(raw)
  if (!Number.isInteger(value) || value < 0) {
    return { error: '随机种子要填 0 或正整数，不想管可以留空。' }
  }
  return { seed: value }
}

async function onCreate(): Promise<void> {
  createError.value = null
  const parsed = parseSeed()
  if (parsed.error !== undefined) {
    createError.value = { title: '随机种子不对', message: parsed.error, detail: parsed.error }
    return
  }
  const custom = selectedCustomScenario.value
  try {
    if (custom !== null) {
      // 用户自上传场景：body 只带 scenario_id（与 card_id/scenario 互斥），可同时绑世界书。
      const id = await store.createGame({
        scenario_id: custom.scenario_id,
        seed: parsed.seed,
        world_book_id: worldBookId.value === '' ? undefined : worldBookId.value,
      })
      await router.push({
        name: 'game',
        params: { id },
        query: { scenario_id: custom.scenario_id },
      })
      return
    }
    const id = await store.createGame({
      scenario: scenarioChoice.value as Scenario,
      seed: parsed.seed,
      world_book_id: worldBookId.value === '' ? undefined : worldBookId.value,
    })
    await router.push({ name: 'game', params: { id } })
  } catch (error) {
    createError.value = describeError(error)
  }
}

async function onOpen(gameId: string): Promise<void> {
  await router.push({ name: 'game', params: { id: gameId } })
}

async function onContinueById(): Promise<void> {
  const id = manualId.value.trim()
  if (id === '') {
    return
  }
  await onOpen(id)
}

function onReloadSaves(): void {
  void loadSaves()
}

onMounted(() => {
  void loadSaves()
  void loadWorldBooks()
  void loadImportedScenarios()
  void loadLlmStatus()
})
</script>

<template>
  <section class="home-view">
    <h2 class="home-title">开始冒险</h2>
    <p class="muted home-tagline">
      一台老式文字冒险机：用一句话描述你想做的事，故事会回应你。
    </p>

    <ErrorBanner
      :title="createError?.title"
      :message="createError?.message ?? ''"
      :detail="createError?.detail"
    />

    <section v-if="showLlmBanner" class="panel llm-banner" role="status" aria-label="大模型未配置">
      <p class="llm-banner-text">
        未配置大模型，当前为离线模板叙事。
        <RouterLink :to="{ name: 'settings' }">去设置填写自己的 DeepSeek API Key</RouterLink>
        （只保存在本机，不影响离线游玩）。
      </p>
      <button class="btn btn-small btn-secondary" type="button" @click="onDismissLlmBanner">
        关闭
      </button>
    </section>

    <section v-reveal class="panel save-panel" aria-label="继续游戏">
      <h3 class="panel-title">继续游戏</h3>
      <p v-if="savesLoading" class="muted" role="status">正在读取存档…</p>
      <template v-else-if="saves.length > 0">
        <ul class="save-list">
          <li v-for="item in saves" :key="item.game_id" class="save-item">
            <div class="save-body">
              <div class="save-head">
                <span class="save-title">{{ labelScenario(item.scenario) }}</span>
                <span class="save-meta muted">
                  第 {{ item.turn }} 回合 · {{ labelGameStatus(item.status) }}
                  <template v-if="item.updated_at"> · {{ formatRelativeTime(item.updated_at) }}</template>
                </span>
              </div>
              <p v-if="item.last_text" class="save-preview">{{ item.last_text }}</p>
            </div>
            <button class="btn btn-small" type="button" @click="onOpen(item.game_id)">继续</button>
          </li>
        </ul>
      </template>
      <p v-else-if="!savesFailed" class="muted">还没有存档，创建一局开始吧。</p>
      <template v-else>
        <p class="muted">暂时读不到存档列表（后端可能没启动）。</p>
        <ul v-if="recentGames.length > 0" class="save-list">
          <li v-for="item in recentGames" :key="item.gameId" class="save-item">
            <div class="save-body">
              <div class="save-head">
                <span class="save-title">{{ labelScenario(item.scenario) }}</span>
                <span class="save-meta muted">
                  {{ describeRecentGame(item, { scenarioLabel: labelScenario }) }}
                </span>
              </div>
            </div>
            <button class="btn btn-small" type="button" @click="onOpen(item.gameId)">继续</button>
          </li>
        </ul>
        <p v-else class="muted">本机也没有别记录。</p>
        <button class="btn btn-small btn-secondary" type="button" @click="onReloadSaves">再试一次</button>
      </template>
    </section>

    <form v-reveal class="panel home-form" @submit.prevent="onCreate">
      <h3 class="panel-title">开始新对局</h3>
      <label class="field">
        <span>故事</span>
        <select v-model="scenarioChoice" class="input" :disabled="store.busy">
          <optgroup label="内置场景">
            <option v-for="option in scenarioOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </optgroup>
          <optgroup v-if="importedScenarios.length > 0" label="我导入的场景">
            <option
              v-for="item in importedScenarios"
              :key="item.scenario_id"
              :value="item.scenario_id"
            >
              {{ item.title }}（{{ item.author === '' ? '匿名' : item.author }}）
            </option>
          </optgroup>
        </select>
        <span class="field-hint muted">
          <template v-if="selectedCustomScenario !== null">
            自定义场景 · {{ formatScenarioStats(selectedCustomScenario.summary) }}
          </template>
          <template v-else>{{ builtinScenarioHint }}</template>
          <template v-if="scenariosFailed"> · 暂时读不到导入的场景（只显示内置，不影响开局）</template>
          <template v-else-if="importedScenarios.length === 0">
            · 还没有导入场景，<RouterLink :to="{ name: 'scenario-import' }">导入一份</RouterLink>？
          </template>
          <template v-else>
            · <RouterLink :to="{ name: 'scenarios' }">管理场景</RouterLink>
          </template>
        </span>
      </label>
      <label class="field">
        <span>世界书（可选）</span>
        <select
          v-model="worldBookId"
          class="input"
          :disabled="store.busy || worldBooksFailed"
        >
          <option value="">不使用世界书</option>
          <option
            v-for="book in worldBooks"
            :key="book.world_book_id"
            :value="book.world_book_id"
          >
            {{ book.name }}（{{ book.entry_count }} 条）
          </option>
        </select>
        <span class="field-hint muted">
          <template v-if="worldBooksFailed">暂时读不到世界书列表（不影响开局）。</template>
          <template v-else-if="worldBooks.length === 0">
            还没有世界书，<RouterLink :to="{ name: 'world-book-import' }">先导入一本</RouterLink>？</template>
          <template v-else>
            世界书会给故事补充设定；<RouterLink :to="{ name: 'world-books' }">管理世界书</RouterLink>
          </template>
        </span>
      </label>
      <details class="advanced">
        <summary>高级选项</summary>
        <label class="field">
          <span>随机种子（留空 = 默认；同种子可复现同一局）</span>
          <input
            v-model="seedText"
            class="input"
            type="number"
            min="0"
            step="1"
            inputmode="numeric"
            placeholder="留空即可"
            :disabled="store.busy"
          />
        </label>
      </details>
      <button class="btn" type="submit" :disabled="store.busy">
        {{ store.busy ? '正在准备…' : '开始冒险' }}
      </button>
    </form>

    <details v-reveal class="panel manual-panel">
      <summary>用对局 ID 继续</summary>
      <form class="manual-form" @submit.prevent="onContinueById">
        <label class="field">
          <span>对局 ID</span>
          <input v-model="manualId" class="input" type="text" placeholder="game_…" />
        </label>
        <button class="btn btn-secondary" type="submit" :disabled="manualId.trim() === ''">
          进入对局
        </button>
      </form>
    </details>

    <p class="muted home-links">
      想先看看人物卡？
      <RouterLink :to="{ name: 'cards' }">打开人物卡库</RouterLink>
      · 也可以
      <RouterLink :to="{ name: 'card-generate' }">用一句设定生成人物卡</RouterLink>
      · 想自己写剧情？
      <RouterLink :to="{ name: 'scenarios' }">场景库</RouterLink>
    </p>
  </section>
</template>
