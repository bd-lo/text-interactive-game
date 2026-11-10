<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  text: string
}>()

// 按码点计数（第 23 章 §23.4：每屏 80–150 字；越界只提示不截断）
const textLength = computed<number>(() => [...props.text].length)
</script>

<template>
  <section class="panel turn-text" aria-live="polite">
    <pre class="turn-text-body">{{ text === '' ? '（等待第一条指令）' : text }}</pre>
    <p class="turn-text-meta">
      <span>{{ textLength }} 字</span>
      <span v-if="textLength > 0 && (textLength < 80 || textLength > 150)" class="warn">
        （超出建议屏字数 80–150，仅提示不截断）
      </span>
    </p>
  </section>
</template>
