<script setup lang="ts">
/**
 * 叙事打字机（H4 炫酷化）：逐字显示最新一屏，可点击/按钮跳过，仅装饰不改变语义。
 *
 * 口径：
 * - 关闭/低功耗/系统 reduced-motion 时**立即整段显示**（等价普通文本）；
 * - 无障碍：读屏始终拿到完整文本（`sr-only`），视觉逐字层 `aria-hidden`，不会重复播报；
 * - 点击文本或「跳过」按钮立即完成；组件卸载/文本变化时清理 rAF；
 * - 只用 `requestAnimationFrame`（security-scan 禁 `setInterval`）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useVisualStore } from '@/stores/visual'

const props = defineProps<{
  /** 要显示的完整文本。 */
  text: string
  /** 覆盖全局开关（未传时读视觉偏好；测试可显式传 false）。 */
  enabled?: boolean
  /** 每秒字符数（默认按文本长度自适应 28–80 cps，长文不拖沓）。 */
  charsPerSecond?: number
  /** 跳过按钮文案。 */
  skipLabel?: string
}>()

const visual = useVisualStore()

const caretChars = computed<string[]>(() => [...props.text])
const visibleCount = ref(0)
const completed = ref(true)
const animating = ref(false)

let rafId = 0
let lastTimestamp: number | null = null
let accumulated = 0

const shouldAnimate = computed<boolean>(() => {
  const enabled = props.enabled ?? visual.typewriterEnabled
  return enabled && caretChars.value.length > 0
})

const speed = computed<number>(() => {
  if (props.charsPerSecond !== undefined) {
    return props.charsPerSecond
  }
  // 短文本逐字更明显，长文本加速（约 0.6–1.8 秒读完）
  const length = caretChars.value.length
  return Math.max(36, Math.min(90, 4800 / Math.max(length, 1)))
})

const visibleText = computed<string>(() => caretChars.value.slice(0, visibleCount.value).join(''))

function cancel(): void {
  if (rafId !== 0) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
}

function frame(timestamp: number): void {
  if (lastTimestamp === null) {
    lastTimestamp = timestamp
  }
  const delta = Math.min((timestamp - lastTimestamp) / 1000, 0.1)
  lastTimestamp = timestamp
  accumulated += delta * speed.value
  const next = Math.min(caretChars.value.length, Math.floor(accumulated))
  if (next !== visibleCount.value) {
    visibleCount.value = next
  }
  if (next >= caretChars.value.length) {
    finish()
    return
  }
  rafId = requestAnimationFrame(frame)
}

/** 立即完成（点击跳过/关闭特效/文本变化）。 */
function finish(): void {
  cancel()
  visibleCount.value = caretChars.value.length
  completed.value = true
  animating.value = false
}

function start(): void {
  cancel()
  accumulated = 0
  lastTimestamp = null
  if (!shouldAnimate.value) {
    finish()
    return
  }
  visibleCount.value = 0
  completed.value = false
  animating.value = true
  rafId = requestAnimationFrame(frame)
}

onMounted(start)

watch(
  () => props.text,
  () => start(),
)

watch(shouldAnimate, (enabled) => {
  if (enabled) {
    start()
  } else {
    finish()
  }
})

onBeforeUnmount(cancel)
</script>

<template>
  <span class="typewriter" :class="{ 'typewriter-animating': animating }" @click="finish">
    <!-- 动画中：读屏拿完整文本（sr-only），视觉层 aria-hidden 逐字；
         动画结束后只保留一份完整文本节点，避免 DOM/正文重复（e2e/复制/读屏都只看到一次）。 -->
    <span v-if="animating" class="sr-only">{{ text }}</span>
    <span v-if="animating" class="typewriter-visible" aria-hidden="true">{{ visibleText }}</span>
    <template v-else>{{ text }}</template>
    <button
      v-if="animating"
      type="button"
      class="typewriter-skip"
      :aria-label="skipLabel ?? '跳过打字动画'"
      @click.stop="finish"
    >
      {{ skipLabel ?? '跳过 ▸' }}
    </button>
  </span>
</template>
