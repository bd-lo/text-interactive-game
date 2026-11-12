<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import TypewriterText from '@/components/TypewriterText.vue'
import type { HistoryEntry } from '@/utils/history'

const props = withDefaults(
  defineProps<{
    entries: HistoryEntry[]
    loading?: boolean
    /** 是否对「最新一屏」启用打字机（由父层读视觉偏好传入；默认关闭便于测试/降级）。 */
    typewriter?: boolean
  }>(),
  { loading: false, typewriter: false },
)

const scroller = ref<HTMLElement | null>(null)

/** 日志行样式类：回合叙事 / 系统提示 / 开场（P2-2）/ 结局（AC-10d）。 */
function entryClass(entry: HistoryEntry): string {
  if (entry.kind === 'notice') {
    return 'history-notice'
  }
  if (entry.kind === 'opening') {
    return 'history-opening'
  }
  if (entry.kind === 'ending') {
    return 'history-ending'
  }
  return 'history-entry'
}

/** 非回合行的前置标签（「开场」「结局」）。 */
function entryLabel(entry: HistoryEntry): string {
  if (entry.kind === 'opening') {
    return '开场'
  }
  if (entry.kind === 'ending') {
    return '结局'
  }
  return ''
}

/** 最新一屏是否用打字机逐字显示（仅普通回合叙事；开场/结局行不做打字机，避免与派生行/结局断言冲突）。 */
function shouldTypewrite(index: number, entry: HistoryEntry): boolean {
  return props.typewriter && index === props.entries.length - 1 && entry.kind === 'narrative'
}

/** 新记录到达后滚到最新一屏（不打断向上回读的历史，只在追加时触发）。 */
async function scrollToLatest(): Promise<void> {
  await nextTick()
  const element = scroller.value
  if (element !== null) {
    element.scrollTop = element.scrollHeight
  }
}

watch(
  () => props.entries.length,
  () => {
    void scrollToLatest()
  },
  { immediate: true },
)
</script>

<template>
  <section class="panel narrative-panel" aria-label="冒险记录">
    <h3 class="panel-title">冒险记录</h3>
    <p v-if="loading" class="muted" role="status">正在载入冒险记录…</p>
    <!-- 文本流语义：role="log"（隐式 aria-live=polite）+ 只播报追加行，读屏不会重复整段历史。 -->
    <ol
      v-else-if="entries.length > 0"
      ref="scroller"
      class="narrative-history"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
    >
      <li
        v-for="(entry, index) in entries"
        :key="entry.id"
        :class="entryClass(entry)"
      >
        <span
          v-if="entry.kind === 'narrative' && entry.turnId !== null"
          class="history-turn"
        >
          第 {{ entry.turnId }} 回合
        </span>
        <span v-else-if="entryLabel(entry) !== ''" class="history-turn">
          {{ entryLabel(entry) }}
        </span>
        <TypewriterText
          v-if="shouldTypewrite(index, entry)"
          class="history-text history-typewriter"
          :class="{ 'history-ending-text': entry.kind === 'ending' }"
          :text="entry.text"
          :enabled="typewriter"
          :chars-per-second="58"
        />
        <pre
          v-else-if="entry.kind === 'narrative' || entry.kind === 'opening' || entry.kind === 'ending'"
          class="history-text"
          :class="{ 'history-ending-text': entry.kind === 'ending' }"
        >{{ entry.text }}</pre>
        <p v-else class="history-text history-notice-text">{{ entry.text }}</p>
      </li>
    </ol>
    <p v-else class="muted">这里会出现你的冒险记录。</p>
    <p class="muted history-readonly-hint">
      查看/背包等只读指令不产生事件、不写入回放，因此不会出现在冒险记录里；正文见下方「最近回应」，刷新后不保留。
    </p>
  </section>
</template>
