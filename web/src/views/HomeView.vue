<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import type { Scenario } from '@/api/types'
import ErrorBanner from '@/components/ErrorBanner.vue'
import { useGameStore } from '@/stores/game'

const router = useRouter()
const store = useGameStore()

const seed = ref<number>(42)
const scenario = ref<Scenario>('m2')
const continueId = ref<string>('')

async function onCreate(): Promise<void> {
  try {
    const id = await store.createGame({ seed: seed.value, scenario: scenario.value })
    await router.push({ name: 'game', params: { id } })
  } catch {
    // 错误已写入 store.error，由 ErrorBanner 展示
  }
}

async function onContinue(): Promise<void> {
  const id = continueId.value.trim()
  if (id === '') {
    return
  }
  await router.push({ name: 'game', params: { id } })
}
</script>

<template>
  <section class="home-view">
    <h2>开始游戏</h2>
    <p class="muted">
      2G 文字 MUD：纯文本、等宽字体、键盘优先。创建对局后进入 <code>/game/:id</code>（版本链以服务端为准）。
    </p>

    <ErrorBanner :message="store.error" />

    <form class="panel home-form" @submit.prevent="onCreate">
      <h3 class="panel-title">新建对局</h3>
      <label class="field">
        <span>随机种子（save_seed 由服务端返回；同种子可复现）</span>
        <input v-model.number="seed" class="input" type="number" min="0" step="1" :disabled="store.busy" />
      </label>
      <label class="field">
        <span>场景</span>
        <select v-model="scenario" class="input" :disabled="store.busy">
          <option value="m2">m2 · 单场景</option>
          <option value="m3">m3 · 多角色</option>
          <option value="m4">m4 · 制造</option>
        </select>
      </label>
      <button class="btn" type="submit" :disabled="store.busy">
        {{ store.busy ? '创建中…' : '创建并进入' }}
      </button>
    </form>

    <form class="panel home-form" @submit.prevent="onContinue">
      <h3 class="panel-title">继续已有对局</h3>
      <label class="field">
        <span>game_id</span>
        <input v-model="continueId" class="input" type="text" placeholder="例如 0d7f…" :disabled="store.busy" />
      </label>
      <button class="btn btn-secondary" type="submit" :disabled="store.busy || continueId.trim() === ''">
        进入对局
      </button>
      <p class="muted">
        刷新/重连：进入后用 <code>GET /api/v1/games/{id}</code> 恢复版本链（无 SSE、无自动轮询）。
      </p>
    </form>
  </section>
</template>
