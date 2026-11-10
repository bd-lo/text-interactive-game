<script setup lang="ts">
import { computed } from 'vue'

import type { ReplayEvent } from '@/api/types'
import { narrativeOf } from '@/utils/replay'

const props = defineProps<{
  event: ReplayEvent | null
}>()

const narrative = computed<string>(() => narrativeOf(props.event))

function formatTs(value: string): string {
  return value.replace('T', ' ').replace('Z', ' UTC').replace(/\.\d+/, '')
}
</script>

<template>
  <!-- 只展示 narrative 与关键元数据；params/result 原始内容不渲染（协议敏感字段定界）。 -->
  <section class="panel replay-event" aria-label="当前事件">
    <h3 class="panel-title">当前事件</h3>
    <template v-if="event !== null">
      <pre class="turn-text-body">{{ narrative }}</pre>
      <dl class="status-grid event-meta">
        <dt>seq</dt>
        <dd>{{ event.seq }}</dd>
        <dt>turn_id</dt>
        <dd>{{ event.turn_id }}</dd>
        <dt>event_type</dt>
        <dd>{{ event.event_type === null ? '（无）' : event.event_type }}</dd>
        <dt>actor</dt>
        <dd>{{ event.actor_id }}</dd>
        <dt>method</dt>
        <dd>{{ event.method }}</dd>
        <dt>ts</dt>
        <dd>{{ formatTs(event.ts) }}</dd>
      </dl>
    </template>
    <p v-else class="muted">未选中事件（时间轴为空或尚未加载）。</p>
  </section>
</template>
