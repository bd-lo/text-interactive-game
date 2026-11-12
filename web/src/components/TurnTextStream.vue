<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 完整文本；流式模式下是「当前已到达」的增量文本。 */
    text: string
    /** N2-039 流式进行中：不显示字数提示/不做 live 播报（最终文本由冒险记录播报一次）。 */
    streaming?: boolean
  }>(),
  { streaming: false },
)

// 按码点计数（第 23 章 §23.4：每屏 80–150 字；越界只提示不截断）
const textLength = computed<number>(() => [...props.text].length)

const placeholder = computed<string>(() => {
  if (props.text !== '') {
    return props.text
  }
  return props.streaming ? '（正在生成…）' : '（等待第一条指令）'
})
</script>

<template>
  <section
    class="panel turn-text"
    :class="{ 'turn-text-streaming': streaming }"
    role="log"
    :aria-live="streaming ? 'off' : 'polite'"
    :aria-busy="streaming ? 'true' : undefined"
  >
    <pre class="turn-text-body">{{ placeholder }}</pre>
    <p v-if="!streaming" class="turn-text-meta">
      <span>{{ textLength }} 字</span>
      <span v-if="textLength > 0 && (textLength < 80 || textLength > 150)" class="warn">
        （超出建议屏字数 80–150，仅提示不截断）
      </span>
    </p>
  </section>
</template>
