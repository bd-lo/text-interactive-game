<script setup lang="ts">
/**
 * 外观与视觉设置（Aurora Glass）：顶栏特效一键开关 + 菜单内的「外观 / 动效」选项。
 *
 * - 无障碍：开关按钮 `aria-pressed`；选项为原生 radio（键盘可用）；系统降级时用 `role="status"`
 *   说明「已自动降级」，避免玩家以为设置失效；
 * - 焦点管理（041 a11y 批次）：`<details>` 展开后焦点进入第一个选项；`Esc` 关闭菜单并把焦点
 *   归还给「视觉设置」摘要按钮（打开/关闭/归还三段都不丢键盘位置）；
 * - 外观（日/夜/跟随系统）与动效档位互相独立，任意组合都有效；
 * - 玩家可见文案不带实现细节（不出现 canvas/rAF/帧率等技术词）。
 */
import { nextTick, ref } from 'vue'

import { useThemeStore, type ThemeMode } from '@/stores/theme'
import { useVisualStore, type VisualMode } from '@/stores/visual'
import { firstFocusable } from '@/utils/a11y'

const theme = useThemeStore()
const visual = useVisualStore()

const menu = ref<HTMLDetailsElement | null>(null)
const summary = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)

const themes: ReadonlyArray<{ value: ThemeMode; label: string; hint: string }> = [
  { value: 'light', label: '日间', hint: '极光玻璃 · 明亮' },
  { value: 'dark', label: '夜间', hint: '极光玻璃 · 深色' },
  { value: 'auto', label: '跟随系统', hint: '随系统的浅色/深色偏好切换' },
]

const options: ReadonlyArray<{ value: VisualMode; label: string; hint: string }> = [
  { value: 'full', label: '全特效', hint: '极光流动 + 微光粒子 + 聚光 + 打字机' },
  { value: 'low', label: '低功耗', hint: '保留氛围，减少粒子与动效' },
  { value: 'off', label: '关闭特效', hint: '只保留静态界面与玻璃质感' },
]

function chooseTheme(mode: ThemeMode): void {
  theme.setMode(mode)
}

function choose(mode: VisualMode): void {
  visual.setMode(mode)
}

/** 展开后聚焦第一个选项（键盘用户打开后直接方向键选择；关闭不改变焦点）。 */
function onToggle(): void {
  if (menu.value?.open !== true) {
    return
  }
  void nextTick(() => {
    const target = firstFocusable(panel.value)
    target?.focus()
  })
}

/** Esc 关闭菜单并把焦点归还给摘要按钮（触发打开的元素）。 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || menu.value?.open !== true) {
    return
  }
  event.preventDefault()
  event.stopPropagation()
  menu.value.open = false
  summary.value?.focus()
}
</script>

<template>
  <div class="visual-settings">
    <button
      class="visual-toggle"
      type="button"
      :aria-pressed="visual.mode !== 'off'"
      :title="`视觉效果：${visual.modeLabel}（点击关闭/恢复）`"
      @click="visual.toggleEffects()"
    >
      <span aria-hidden="true">✦</span> {{ visual.modeLabel }}
    </button>
    <details
      ref="menu"
      class="visual-menu"
      aria-label="外观与动效设置"
      @toggle="onToggle"
      @keydown="onKeydown"
    >
      <summary ref="summary">视觉设置</summary>
      <div ref="panel">
        <fieldset class="tone-field visual-group">
          <legend>外观</legend>
          <label v-for="option in themes" :key="option.value" class="visual-option">
            <input
              type="radio"
              name="theme-mode"
              :value="option.value"
              :checked="theme.mode === option.value"
              @change="chooseTheme(option.value)"
            />
            <span class="visual-option-body">
              <span>{{ option.label }}</span>
              <span class="muted">{{ option.hint }}</span>
            </span>
          </label>
        </fieldset>
        <fieldset class="tone-field visual-group" aria-describedby="visual-reduced-note">
          <legend>动效</legend>
          <label v-for="option in options" :key="option.value" class="visual-option">
            <input
              type="radio"
              name="visual-mode"
              :value="option.value"
              :checked="visual.mode === option.value"
              @change="choose(option.value)"
            />
            <span class="visual-option-body">
              <span>{{ option.label }}</span>
              <span class="muted">{{ option.hint }}</span>
            </span>
          </label>
        </fieldset>
        <p v-if="visual.systemReducedMotion" id="visual-reduced-note" class="muted visual-note" role="status">
          检测到系统已开启「减少动态效果」：打字机、微光粒子与背景动效已自动降级为静态显示（正文仍完整显示），
          仍可手动选择纯静态界面。
        </p>
        <span v-else id="visual-reduced-note" class="sr-only">系统未要求减少动态效果。</span>
      </div>
    </details>
  </div>
</template>
