<script setup lang="ts">
/**
 * 顶栏外观切换按钮（Aurora Glass）。
 *
 * - 单击在日间 ↔ 夜间之间切换，档位持久化（`stores/theme`）；
 * - 支持 View Transition API 时做「从按钮位置扩散」的换肤动画，否则瞬时切换；
 * - 系统「减少动态效果」时不做动画（内容仍然立即切换，绝不因此不可用）；
 * - 无障碍：稳定可读名称「外观 切换日间/夜间」，`aria-pressed` 表示当前是否日间。
 */
import { nextTick } from 'vue'

import { useThemeStore } from '@/stores/theme'
import { useVisualStore } from '@/stores/visual'

interface ViewTransitionLike {
  finished: Promise<void>
}

/** 只声明我们用到的最小面（`Document.startViewTransition` 在部分 TS lib 中已存在，避免重声明冲突）。 */
interface ViewTransitionHost {
  startViewTransition?: (update: () => Promise<void> | void) => ViewTransitionLike
}

const theme = useThemeStore()
const visual = useVisualStore()

/** 把点击位置写进 CSS 变量，供 `clip-path: circle()` 扩散动画取圆心/半径。 */
function setOrigin(event: MouseEvent): void {
  const root = document.documentElement
  const x = typeof event.clientX === 'number' && event.clientX > 0 ? event.clientX : window.innerWidth / 2
  const y = typeof event.clientY === 'number' && event.clientY > 0 ? event.clientY : window.innerHeight / 2
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y))
  root.style.setProperty('--theme-origin-x', `${Math.round(x)}px`)
  root.style.setProperty('--theme-origin-y', `${Math.round(y)}px`)
  root.style.setProperty('--theme-radius', `${Math.ceil(radius)}px`)
}

function onToggle(event: MouseEvent): void {
  const next = theme.isLight ? 'dark' : 'light'
  const start = (document as unknown as ViewTransitionHost).startViewTransition
  const canAnimate = typeof start === 'function' && !visual.systemReducedMotion

  if (!canAnimate) {
    theme.setMode(next)
    return
  }

  setOrigin(event)
  const root = document.documentElement
  root.classList.add('theme-switching')

  let applied = false
  try {
    const transition = start.call(document, async () => {
      applied = true
      theme.setMode(next)
      // 必须等 Vue 把 data-theme 写进 DOM，否则快照拍到的还是旧皮肤。
      await nextTick()
    })
    void transition.finished.finally(() => {
      root.classList.remove('theme-switching')
    })
  } catch {
    root.classList.remove('theme-switching')
    if (!applied) {
      theme.setMode(next)
    }
  }
}
</script>

<template>
  <button
    class="theme-toggle"
    type="button"
    :aria-pressed="theme.isLight"
    :title="`当前外观：${theme.themeLabel}，点击切换到${theme.nextThemeLabel}`"
    @click="onToggle"
  >
    <span class="theme-toggle-icon" aria-hidden="true">
      <svg
        v-if="theme.isLight"
        class="theme-toggle-svg"
        viewBox="0 0 24 24"
        width="16"
        height="16"
        focusable="false"
      >
        <circle cx="12" cy="12" r="4.1" fill="currentColor" />
        <g stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
          <path d="M12 2.7v2.3M12 19v2.3M2.7 12h2.3M19 12h2.3M5.5 5.5l1.6 1.6M16.9 16.9l1.6 1.6M18.5 5.5l-1.6 1.6M7.1 16.9l-1.6 1.6" />
        </g>
      </svg>
      <svg
        v-else
        class="theme-toggle-svg"
        viewBox="0 0 24 24"
        width="16"
        height="16"
        focusable="false"
      >
        <path d="M20.2 14.7A8.6 8.6 0 1 1 9.3 3.8a8.6 8.6 0 0 0 10.9 10.9Z" fill="currentColor" />
      </svg>
    </span>
    <span class="theme-toggle-text">外观</span>
    <span class="sr-only">切换日间/夜间</span>
  </button>
</template>
