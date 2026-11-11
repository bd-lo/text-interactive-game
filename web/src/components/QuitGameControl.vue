<script setup lang="ts">
/**
 * 全局「退出游戏」控件（网页正常退出启动器托管的本机服务）。
 *
 * 状态机：loading(隐藏) → idle → confirm（shutdown_supported=true）→ 已退出覆盖层
 *                              └→ info（非启动器模式，固定 Ctrl+C 文案）
 * 失败分支：shutdown 网络/403/404 → 玩家可读错误 + 回到 idle（按钮恢复可点，**无自动重试**）。
 * 心跳：launcher_mode=true 时每 15s fire-and-forget POST /heartbeat（失败静默）；卸载/已退出即停。
 * 约定：status 请求失败时整个控件静默隐藏（不在全局 header 弹错误，不打扰离线游玩）。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'

import { getAppStatus, sendLauncherHeartbeat, shutdownGame } from '@/api/client'
import type { LauncherStatus } from '@/api/types'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'
import {
  canShutdownInPage,
  EXIT_OVERLAY_BODY,
  EXIT_OVERLAY_TITLE,
  LAUNCHER_HEARTBEAT_INTERVAL_MS,
  QUIT_CANCEL_LABEL,
  QUIT_CONFIRM_LABEL,
  QUIT_GAME_LABEL,
  QUIT_INFO_CLOSE_LABEL,
  QUIT_INFO_TEXT,
  shouldHeartbeat,
} from '@/utils/launcher'

type QuitPhase = 'idle' | 'confirm' | 'info' | 'exited'

/** 启动器状态（null = 读取中/读取失败：按钮静默隐藏）。 */
const status = ref<LauncherStatus | null>(null)
const phase = ref<QuitPhase>('idle')
const busy = ref(false)
const error = ref<PlayerErrorInfo | null>(null)

const errorText = computed<string>(() => {
  const info = error.value
  if (info === null) {
    return ''
  }
  return info.title === '' ? info.message : `${info.title}：${info.message}`
})

let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let unmounted = false

/** 停止心跳（卸载/已退出时调用）。 */
function stopHeartbeat(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

/** 心跳 tick：fire-and-forget（失败静默，禁止 console.error；下一拍照常继续）。 */
function heartbeatTick(): void {
  if (unmounted || phase.value === 'exited' || !shouldHeartbeat(status.value)) {
    return
  }
  void sendLauncherHeartbeat().catch(() => undefined)
}

/**
 * 启动心跳（仅启动器模式；固定 15s 节拍，不是失败自动重试）。
 *
 * 项目静态门禁默认禁 `setInterval`（防自动重试/轮询）；这里是启动器存活心跳，
 * 行尾 `// 心跳 keep-alive` 是 `tests/integration/test_frontend_contract.py` 的唯一白名单标记。
 */
function startHeartbeat(): void {
  stopHeartbeat()
  if (!shouldHeartbeat(status.value)) {
    return
  }
  heartbeatTimer = setInterval(heartbeatTick, LAUNCHER_HEARTBEAT_INTERVAL_MS) // 心跳 keep-alive
}

/** 读取启动器状态：失败静默隐藏按钮（离线/普通 uvicorn 模式下不打扰玩家）。 */
async function loadStatus(): Promise<void> {
  try {
    status.value = await getAppStatus()
    startHeartbeat()
  } catch {
    status.value = null
  }
}

/**
 * 页面重新可见时立刻补一拍心跳。
 *
 * 浏览器会把后台标签页的 timer 统一节流（setInterval 也可能 >15s）；切回页面时立即补报，
 * 避免「玩家只是切了个标签页，服务端却按空闲超额退出」。心跳本身仍是 fire-and-forget。
 */
function onVisibilityChange(): void {
  if (document.visibilityState !== 'visible') {
    return
  }
  heartbeatTick()
}

/** 点「退出游戏」：支持页内退出 → 二次确认；否则 → Ctrl+C 说明面板。 */
function onQuitClick(): void {
  error.value = null
  phase.value = canShutdownInPage(status.value) ? 'confirm' : 'info'
}

function onCancel(): void {
  error.value = null
  phase.value = 'idle'
}

/** 确认退出：请求服务端 shutdown → 立即显示「已退出」覆盖层（不等进程真正停止）。 */
async function onConfirmQuit(): Promise<void> {
  if (busy.value) {
    return
  }
  busy.value = true
  error.value = null
  try {
    await shutdownGame()
    stopHeartbeat()
    phase.value = 'exited'
  } catch (err) {
    if (!unmounted) {
      error.value = describeError(err)
      phase.value = 'idle'
    }
  } finally {
    busy.value = false
  }
}

function onInfoClose(): void {
  phase.value = 'idle'
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  void loadStatus()
})

onUnmounted(() => {
  unmounted = true
  stopHeartbeat()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div v-if="status !== null" class="quit-game-control">
    <button
      v-if="phase === 'idle'"
      class="btn btn-small btn-secondary quit-game-button"
      type="button"
      data-testid="quit-game-button"
      @click="onQuitClick"
    >
      {{ QUIT_GAME_LABEL }}
    </button>

    <template v-else-if="phase === 'confirm'">
      <button
        class="btn btn-small quit-confirm-button"
        type="button"
        data-testid="quit-confirm-button"
        :disabled="busy"
        @click="onConfirmQuit"
      >
        {{ busy ? '正在退出…' : QUIT_CONFIRM_LABEL }}
      </button>
      <button
        class="btn btn-small btn-secondary quit-cancel-button"
        type="button"
        data-testid="quit-cancel-button"
        :disabled="busy"
        @click="onCancel"
      >
        {{ QUIT_CANCEL_LABEL }}
      </button>
    </template>

    <section
      v-else-if="phase === 'info'"
      class="quit-info-panel"
      data-testid="quit-info-panel"
      role="status"
    >
      <p class="quit-info-text">{{ QUIT_INFO_TEXT }}</p>
      <button
        class="btn btn-small btn-secondary"
        type="button"
        data-testid="quit-info-close"
        @click="onInfoClose"
      >
        {{ QUIT_INFO_CLOSE_LABEL }}
      </button>
    </section>

    <p v-if="error !== null" class="quit-error" role="alert">{{ errorText }}</p>
  </div>

  <div
    v-if="phase === 'exited'"
    class="exit-overlay"
    data-testid="exit-overlay"
    role="status"
    aria-live="polite"
  >
    <h2 class="exit-overlay-title">{{ EXIT_OVERLAY_TITLE }}</h2>
    <p class="exit-overlay-body">{{ EXIT_OVERLAY_BODY }}</p>
  </div>
</template>
