<script setup lang="ts">
/**
 * 氛围层（Aurora Glass）：极光色团 + 细颗粒 + 鼠标聚光。
 *
 * 设计取舍：
 * - 极光用 4 个「本身就是柔和渐变」的色团，只动 `transform`（合成层），不使用 `filter: blur()`，
 *   避免整层重绘把低端机拖垮；
 * - 细颗粒用内联 SVG 噪点（同一张 180px 贴图重复），透明度极低，不影响文字对比度；
 * - 聚光由 `pointermove` + `requestAnimationFrame` 节流写 CSS 变量，只有「全特效」档才更新；
 * - 纯装饰：`aria-hidden`、`pointer-events: none`；`data-visual='off'` 时整层不渲染。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { useVisualStore } from '@/stores/visual'

const visual = useVisualStore()
const layer = ref<HTMLDivElement | null>(null)

let rafId = 0
let pendingX = 0
let pendingY = 0
let hasPending = false

function flush(): void {
  rafId = 0
  if (!hasPending) {
    return
  }
  hasPending = false
  const element = layer.value
  if (element === null) {
    return
  }
  element.style.setProperty('--spot-x', `${pendingX}px`)
  element.style.setProperty('--spot-y', `${pendingY}px`)
}

function onPointerMove(event: PointerEvent): void {
  if (!visual.spotlightEnabled) {
    return
  }
  pendingX = event.clientX
  pendingY = event.clientY
  hasPending = true
  if (rafId === 0) {
    rafId = requestAnimationFrame(flush)
  }
}

onMounted(() => {
  if (typeof window === 'undefined') {
    return
  }
  window.addEventListener('pointermove', onPointerMove, { passive: true })
})

onBeforeUnmount(() => {
  if (typeof window === 'undefined') {
    return
  }
  window.removeEventListener('pointermove', onPointerMove)
  if (rafId !== 0) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
})
</script>

<template>
  <div ref="layer" class="atmosphere-layer" aria-hidden="true">
    <div class="aurora-blob aurora-blob-1"></div>
    <div class="aurora-blob aurora-blob-2"></div>
    <div class="aurora-blob aurora-blob-3"></div>
    <div class="aurora-blob aurora-blob-4"></div>
    <div class="atmosphere-spotlight"></div>
    <div class="atmosphere-grain"></div>
  </div>
</template>
