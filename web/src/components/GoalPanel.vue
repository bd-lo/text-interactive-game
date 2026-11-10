<script setup lang="ts">
import { computed } from 'vue'

import type { Goal } from '@/api/types'

const props = defineProps<{
  goal: Goal
}>()

const doneCount = computed<number>(() => props.goal.objectives.filter((item) => item.done).length)
</script>

<template>
  <section class="panel goal-panel" aria-label="当前目标">
    <h3 class="panel-title">{{ goal.title }}</h3>
    <p class="goal-desc">{{ goal.description }}</p>
    <ul class="objective-list">
      <li
        v-for="objective in goal.objectives"
        :key="objective.id"
        :class="objective.done ? 'objective objective-done' : 'objective'"
      >
        <span class="objective-mark" aria-hidden="true">{{ objective.done ? '✓' : '○' }}</span>
        <span>{{ objective.text }}</span>
      </li>
    </ul>
    <p class="goal-progress muted">进度 {{ doneCount }} / {{ goal.objectives.length }}</p>
  </section>
</template>
