<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import type { HistoryEntry } from '@/utils/history'

const props = withDefaults(
  defineProps<{
    entries: HistoryEntry[]
    loading?: boolean
  }>(),
  { loading: false },
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
    <ol v-else-if="entries.length > 0" ref="scroller" class="narrative-history">
      <li
        v-for="entry in entries"
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
        <pre
          v-if="entry.kind === 'narrative' || entry.kind === 'opening' || entry.kind === 'ending'"
          class="history-text"
          :class="{ 'history-ending-text': entry.kind === 'ending' }"
        >{{ entry.text }}</pre>
        <p v-else class="history-text history-notice-text">{{ entry.text }}</p>
      </li>
    </ol>
    <p v-else class="muted">这里会出现你的冒险记录。</p>
    <p class="muted history-readonly-hint">查看/背包等只读指令不会写入冒险记录。</p>
  </section>
</template>
