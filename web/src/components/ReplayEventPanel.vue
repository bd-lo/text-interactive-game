<script setup lang="ts">
import { computed } from 'vue'

import type { ReplayEvent } from '@/api/types'
import { formatDateTime, formatEventRef, labelActor, labelEventType, labelMethod } from '@/utils/labels'
import { narrativeOf } from '@/utils/replay'

const props = defineProps<{
  event: ReplayEvent | null
}>()

const narrative = computed<string>(() => narrativeOf(props.event))
const eventRef = computed<string>(() =>
  props.event === null ? '' : formatEventRef(props.event.seq, props.event.turn_id),
)
</script>

<template>
  <!-- 只展示 narrative 与本地化元数据；params/result 原始内容不渲染（协议敏感字段定界）。 -->
  <section class="panel replay-event" aria-label="当前事件">
    <h3 class="panel-title">当前事件</h3>
    <template v-if="event !== null">
      <p class="event-ref">{{ eventRef }}</p>
      <pre class="turn-text-body">{{ narrative }}</pre>
      <dl class="status-grid event-meta">
        <dt>类型</dt>
        <dd>{{ labelEventType(event.event_type) }}</dd>
        <dt>动作</dt>
        <dd>{{ labelMethod(event.method) }}</dd>
        <dt>角色</dt>
        <dd>{{ labelActor(event.actor_id) }}</dd>
        <dt>时间</dt>
        <dd>{{ formatDateTime(event.ts) }}</dd>
      </dl>
    </template>
    <p v-else class="muted">未选中事件（时间轴为空或尚未加载）。</p>
  </section>
</template>
