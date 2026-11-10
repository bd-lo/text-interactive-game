<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { listGames } from '@/api/client'
import type { GameListItem, Scenario } from '@/api/types'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { useGameStore } from '@/stores/game'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'
import { labelGameStatus, labelScenario } from '@/utils/labels'
import {
  describeRecentGame,
  formatRelativeTime,
  loadRecentGames,
  type RecentGameRecord,
} from '@/utils/recentGames'

const router = useRouter()
const store = useGameStore()

const SAVE_LIMIT = 10

const scenario = ref<Scenario>('story')
/** `input type=number` 在 Vue 里会自动 number 化，空值仍是字符串，这里两种都接受。 */
const seedText = ref<string | number>('')
const manualId = ref('')

const saves = ref<GameListItem[]>([])
const savesLoading = ref(false)
const savesFailed = ref(false)
const recentGames = ref<RecentGameRecord[]>([])
const createError = ref<PlayerErrorInfo | null>(null)

const scenarioOptions: Array<{ value: Scenario; label: string; hint: string }> = [
  { value: 'story', label: '余烬哨站（剧情）', hint: '有目标与结局，推荐第一次玩' },
  { value: 'm2', label: '废弃哨站（入门）', hint: '一小段探索' },
  { value: 'm3', label: '拾荒者营地（多角色）', hint: '会碰到别的角色' },
  { value: 'm4', label: '工坊（制造）', hint: '可以制作东西' },
]

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
  try {
    const id = await store.createGame({ scenario: scenario.value, seed: parsed.seed })
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
})
</script>

<template>
  <section class="home-view">
    <h2>开始冒险</h2>
    <p class="muted home-tagline">
      一台老式文字冒险机：用一句话描述你想做的事，故事会回应你。
    </p>

    <ErrorBanner
      :title="createError?.title"
      :message="createError?.message ?? ''"
      :detail="createError?.detail"
    />

    <section class="panel save-panel" aria-label="继续游戏">
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

    <form class="panel home-form" @submit.prevent="onCreate">
      <h3 class="panel-title">开始新对局</h3>
      <label class="field">
        <span>故事</span>
        <select v-model="scenario" class="input" :disabled="store.busy">
          <option v-for="option in scenarioOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
        <span class="field-hint muted">
          {{ scenarioOptions.find((option) => option.value === scenario)?.hint }}
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

    <details class="panel manual-panel">
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
    </p>
  </section>
</template>
