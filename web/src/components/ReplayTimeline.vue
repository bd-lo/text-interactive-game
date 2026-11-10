<script setup lang="ts">
import { computed } from 'vue'

import { clampIndex, stepIndex } from '@/utils/replay'

const props = defineProps<{
  /** 已加载事件数。 */
  count: number
  selectedIndex: number
  selectedSeq: number
  /** 已加载事件的最大 seq（用于展示进度；页面可再「加载更多」）。 */
  lastSeq: number
  busy?: boolean
}>()

const emit = defineEmits<{
  (event: 'select', index: number): void
}>()

const current = computed<number>(() => clampIndex(props.selectedIndex, props.count))
const hasEvents = computed<boolean>(() => props.count > 0)

function select(index: number): void {
  emit('select', clampIndex(index, props.count))
}

function goFirst(): void {
  select(0)
}

function goPrev(): void {
  select(stepIndex(current.value, props.count, -1))
}

function goNext(): void {
  select(stepIndex(current.value, props.count, 1))
}

function goLast(): void {
  select(props.count - 1)
}

function onRangeInput(event: Event): void {
  const target = event.target as HTMLInputElement
  select(target.valueAsNumber)
}
</script>

<template>
  <section class="panel replay-timeline" aria-label="回放时间轴">
    <h3 class="panel-title">时间轴（全局 seq）</h3>
    <div class="timeline-controls">
      <button class="btn btn-small btn-secondary" type="button" :disabled="!hasEvents || busy" @click="goFirst">
        ⏮ 首
      </button>
      <button class="btn btn-small btn-secondary" type="button" :disabled="!hasEvents || busy" @click="goPrev">
        ◀ 上一步
      </button>
      <button class="btn btn-small btn-secondary" type="button" :disabled="!hasEvents || busy" @click="goNext">
        下一步 ▶
      </button>
      <button class="btn btn-small btn-secondary" type="button" :disabled="!hasEvents || busy" @click="goLast">
        末 ⏭
      </button>
    </div>
    <input
      v-if="hasEvents"
      class="timeline-range"
      type="range"
      min="0"
      :max="count - 1"
      step="1"
      :value="current"
      :disabled="busy === true"
      aria-label="拖动定位事件"
      @input="onRangeInput"
    />
    <p class="timeline-meta">
      <span>seq {{ selectedSeq }} / 已加载末 seq {{ lastSeq }}</span>
      <span>第 {{ hasEvents ? current + 1 : 0 }} / {{ count }} 条（已加载）</span>
    </p>
    <p v-if="count === 0" class="muted">暂无已加载事件。</p>
  </section>
</template>
