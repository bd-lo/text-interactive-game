<script setup lang="ts">
/**
 * 视觉设置（H4 炫酷化）：顶栏一键开关 + 三档选择，偏好持久化（localStorage）。
 *
 * - 无障碍：开关按钮 `aria-pressed`；三档为原生 radio（键盘可用）；系统降级时给出说明；
 * - 玩家可见文案不带实现细节（不出现 canvas/rAF/帧率等技术词）。
 */
import { useVisualStore, type VisualMode } from '@/stores/visual'

const visual = useVisualStore()

const options: ReadonlyArray<{ value: VisualMode; label: string; hint: string }> = [
  { value: 'full', label: '全特效', hint: '余烬粒子 + CRT 扫描线 + 打字机' },
  { value: 'low', label: '低功耗', hint: '保留氛围，减少粒子与动效' },
  { value: 'off', label: '关闭特效', hint: '只保留纯文本界面' },
]

function choose(mode: VisualMode): void {
  visual.setMode(mode)
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
    <details class="visual-menu">
      <summary>视觉设置</summary>
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
      <p v-if="visual.systemReducedMotion" class="muted visual-note">
        检测到系统已开启「减少动态效果」，动效已自动降级（仍可手动选择纯文本）。
      </p>
    </details>
  </div>
</template>
