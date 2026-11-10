<script setup lang="ts">
import { computed } from 'vue'

import ActionChips from '@/components/ActionChips.vue'
import { collectTurnFeedback, hasTurnFeedback } from '@/utils/feedback'

const props = defineProps<{
  /** `TurnResult.effects`（失败回合由后端填入 `{hint}`）。 */
  effects: Array<Record<string, unknown>>
  /** `TurnResult.suggested_actions`（应用动作已在父层过滤，这里只放游戏指令）。 */
  suggestedActions: string[]
}>()

const emit = defineEmits<{
  (event: 'command', text: string): void
}>()

const feedback = computed(() => collectTurnFeedback(props.effects, props.suggestedActions))
const visible = computed<boolean>(() => hasTurnFeedback(feedback.value))
</script>

<template>
  <!-- 只展示后端加法字段（提示/可试试），不改 TurnResult.text 原文展示（第 23 章 §23.3）。 -->
  <section v-if="visible" class="panel turn-feedback" aria-label="本回合提示" aria-live="polite">
    <div v-if="feedback.hints.length > 0" class="feedback-group">
      <h4 class="panel-subtitle">提示</h4>
      <ul class="plain-list">
        <li v-for="hint in feedback.hints" :key="hint">{{ hint }}</li>
      </ul>
    </div>
    <div v-if="feedback.actions.length > 0" class="feedback-group">
      <h4 class="panel-subtitle">可以试试</h4>
      <ActionChips :actions="feedback.actions" @command="emit('command', $event)" />
    </div>
  </section>
</template>
