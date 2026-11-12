<script setup lang="ts">
/**
 * 回合指令输入（键盘优先）。
 *
 * 键盘契约（041 a11y 批次落实第 23 章 §23.4 既有约定）：
 * - `Enter` 提交；`Shift+Enter` / `Ctrl+Enter` / `Cmd+Enter` 换行；IME 组字期间（`isComposing`）不提交；
 * - `↑` / `↓` 翻本地已发送历史（输入框为空或光标在首/尾时，不抢光标移动）；
 * - `Esc` 取消当前草稿（清空但保持焦点；IME 组字中的 Esc 交给浏览器取消候选）；
 * - 任意位置按 `/` 聚焦指令框（不在输入控件内、无修饰键时；带 `aria-keyshortcuts` 提示）。
 *
 * 忙碌（`disabled`）时用 `readonly` 保留焦点与键盘位置，表单 `aria-busy` 标示处理中；
 * 提交完成（disabled → false）后焦点回到输入框，键盘玩家不必每回合重新 Tab。
 */
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { isEditableTarget } from '@/utils/a11y'

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

/** 本地已发送历史（含父层传入的 lastCommand）；`historyIndex = -1` 表示停在当前草稿。 */
const history = ref<string[]>([])
let historyIndex = -1

function rememberHistory(value: string): void {
  const trimmed = value.trim()
  if (trimmed === '') {
    return
  }
  if (history.value[history.value.length - 1] !== trimmed) {
    history.value = [...history.value, trimmed]
  }
}

watch(
  () => props.lastCommand,
  (value) => {
    rememberHistory(value)
  },
  { immediate: true },
)

function focusInput(): void {
  void nextTick(() => input.value?.focus())
}

onMounted(() => {
  focusInput()
  document.addEventListener('keydown', onGlobalKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onGlobalKeydown)
})

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
  rememberHistory(value)
  historyIndex = -1
  emit('submit', value)
  text.value = ''
  focusInput()
}

/** 写入草稿并把光标放到末尾（历史翻阅后继续输入）。 */
async function setDraft(value: string): Promise<void> {
  text.value = value
  await nextTick()
  const element = input.value
  if (element !== null) {
    const end = element.value.length
    element.setSelectionRange(end, end)
  }
}

/** 历史方向键：仅在「输入框为空或光标在首/尾」时接管，避免和光标移动冲突。 */
function onHistoryKey(event: KeyboardEvent): boolean {
  const element = input.value
  if (element === null || history.value.length === 0) {
    return false
  }
  const atStart = element.selectionStart === 0 && element.selectionEnd === 0
  const atEnd =
    element.selectionStart === element.value.length &&
    element.selectionEnd === element.value.length

  if (event.key === 'ArrowUp') {
    // 已在浏览历史时继续向前翻（不再要求光标在首/空）；首次按 ↑ 才要求空输入或光标在开头。
    if (!(historyIndex >= 0 || text.value === '' || atStart)) {
      return false
    }
    event.preventDefault()
    historyIndex = historyIndex < 0 ? history.value.length - 1 : Math.max(0, historyIndex - 1)
    const previous = history.value[historyIndex]
    if (previous !== undefined) {
      void setDraft(previous)
    }
    return true
  }

  if (event.key === 'ArrowDown') {
    if (historyIndex < 0 || !atEnd) {
      return false
    }
    event.preventDefault()
    historyIndex += 1
    const next = history.value[historyIndex]
    if (next === undefined) {
      historyIndex = -1
      void setDraft('')
    } else {
      void setDraft(next)
    }
    return true
  }

  return false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') {
    // IME 组合输入期间不提交（中文输入法兼容）；Shift/Ctrl/Cmd+Enter 保留换行
    if (event.isComposing || event.shiftKey || event.ctrlKey || event.metaKey) {
      return
    }
    event.preventDefault()
    submit()
    return
  }

  if (event.key === 'Escape') {
    // IME 组字中的 Esc 用于取消候选，交给浏览器；否则取消当前草稿并保持焦点。
    if (event.isComposing) {
      return
    }
    if (text.value !== '') {
      event.preventDefault()
      text.value = ''
      historyIndex = -1
    }
    return
  }

  if (onHistoryKey(event)) {
    return
  }
}

/** 用户重新输入即退出历史浏览（Esc 取消草稿时也会重置）。 */
function onInput(): void {
  historyIndex = -1
}

/** 全局 `/` 聚焦：只在非输入控件、无修饰键时生效（不抢浏览器快捷键/输入法）。 */
function onGlobalKeydown(event: KeyboardEvent): void {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) {
    return
  }
  if (event.key !== '/' || isEditableTarget(event.target)) {
    return
  }
  event.preventDefault()
  focusInput()
}
</script>

<template>
  <form class="panel command-input" :aria-busy="disabled" @submit.prevent="submit">
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
      aria-keyshortcuts="Enter Escape ArrowUp ArrowDown"
      @keydown="onKeydown"
      @input="onInput"
    ></textarea>
    <div class="command-actions">
      <button class="btn" type="submit" :disabled="disabled || text.trim() === ''">发送</button>
      <span id="command-hint" class="muted">
        Enter 发送 · Shift+Enter 换行 · ↑/↓ 历史 · / 聚焦
      </span>
    </div>
  </form>
</template>
