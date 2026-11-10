<script setup lang="ts">
/**
 * CRT 视觉层（H4 炫酷化）：扫描线 + 暗角 + 微噪点 + 缓慢电子束，纯 CSS 渲染（无脚本）。
 *
 * - 纯装饰：`aria-hidden`、`pointer-events: none`，不参与布局与可访问性树；
 * - 低对比度（扫描线/噪点 opacity ≤ 0.06），不影响文字对比度；
 * - 关闭特效时由 App 不渲染本组件；系统 reduced-motion 由 CSS 停掉动画；
 * - 参考 CRT/scanline 的通用 CSS 手法自行实现（未复制第三方代码）。
 */
</script>

<template>
  <div class="crt-layer" aria-hidden="true">
    <div class="crt-scanlines"></div>
    <div class="crt-noise"></div>
    <div class="crt-vignette"></div>
    <div class="crt-beam"></div>
  </div>
</template>
