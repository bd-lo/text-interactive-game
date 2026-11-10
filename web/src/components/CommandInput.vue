<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 忙/结束时禁止提交（用 readonly 保持焦点，不丢键盘位置）。 */
    disabled: boolean
    /** 上一条已发送指令：输入框为空时按 ↑ 调出（键盘玩家友好）。 */
    lastCommand?: string
    placeholder?: string
  }>(),
  {
    lastCommand: '',
    placeholder: '例如：环顾四周 / 拿起火把 / 向北走',
  },
)

const emit = defineEmits<{
  (event: 'submit', text: string): void
}>()

const text = ref('')
const input = ref<HTMLTextAreaElement | null>(null)

function focusInput(): void {
  void nextTick(() => input.value?.focus())
}

onMounted(focusInput)

// 提交结束（disabled → false）后把焦点还给输入框：键盘玩家不必每回合重新 Tab。
watch(
  () => props.disabled,
  (disabled) => {
    if (!disabled) {
      focusInput()
    }
  },
)

function submit(): void {
  const value = text.value.trim()
  if (value === '' || props.disabled) {
    return
  }
  emit('submit', value)
  text.value = ''
  focusInput()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') {
    // IME 组合输入期间不提交（中文输入法兼容）；Shift+Enter 保留换行
    if (event.isComposing || event.shiftKey) {
      return
    }
    event.preventDefault()
    submit()
    return
  }
  if (event.key === 'ArrowUp' && text.value === '' && props.lastCommand !== '') {
    event.preventDefault()
    text.value = props.lastCommand
  }
}
</script>

<template>
  <form class="panel command-input" @submit.prevent="submit">
    <label class="command-label" for="command-input">你要做什么？</label>
    <textarea
      id="command-input"
      ref="input"
      v-model="text"
      class="command-text"
      rows="2"
      maxlength="500"
      :readonly="disabled"
      :placeholder="placeholder"
      aria-describedby="command-hint"
      @keydown="onKeydown"
    ></textarea>
    <div class="command-actions">
      <button class="btn" type="submit" :disabled="disabled || text.trim() === ''">发送</button>
      <span id="command-hint" class="muted">Enter 发送 · Shift+Enter 换行 · ↑ 调出上一条</span>
    </div>
  </form>
</template>
