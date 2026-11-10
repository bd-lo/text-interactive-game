<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled: boolean
}>()

const emit = defineEmits<{
  (event: 'submit', text: string): void
}>()

const text = ref('')

function submit(): void {
  const value = text.value.trim()
  if (value === '' || props.disabled) {
    return
  }
  emit('submit', value)
  text.value = ''
}

function onKeydown(event: KeyboardEvent): void {
  // IME 组合输入期间不提交（中文输入法兼容）
  if (event.key !== 'Enter' || event.isComposing) {
    return
  }
  event.preventDefault()
  submit()
}
</script>

<template>
  <form class="panel command-input" @submit.prevent="submit">
    <label class="command-label" for="command-input">指令</label>
    <textarea
      id="command-input"
      v-model="text"
      class="command-text"
      rows="2"
      maxlength="500"
      :disabled="disabled"
      placeholder="例如：向北走 / 查看背包 / 和哨兵交谈"
      @keydown="onKeydown"
    ></textarea>
    <div class="command-actions">
      <button class="btn" type="submit" :disabled="disabled || text.trim() === ''">提交</button>
      <span class="muted">Enter 提交（IME 组合中不会误发）；失败后请手动点「重试」。</span>
    </div>
  </form>
</template>
