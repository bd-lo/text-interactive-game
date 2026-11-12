<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 可点击指令（展示文本 = 提交文本）。 */
    actions: string[]
    /** 无障碍/视觉分组标题（可选）。 */
    label?: string
    /** 指令 → 悬停/读屏补充说明（如出口通往哪个房间）。 */
    titles?: Record<string, string>
    /** 不可交互（如结局后）时禁用全部按钮。 */
    disabled?: boolean
  }>(),
  { label: '', titles: () => ({}), disabled: false },
)

const emit = defineEmits<{
  (event: 'command', text: string): void
}>()
</script>

<template>
  <div v-if="actions.length > 0" class="action-chips" role="group" :aria-label="label === '' ? '可选动作' : label">
    <span v-if="label !== ''" class="action-chips-label" aria-hidden="true">{{ label }}</span>
    <ul class="chip-list">
      <li v-for="action in actions" :key="action">
        <button
          type="button"
          class="chip"
          :title="titles[action] ?? ''"
          :disabled="disabled"
          @click="emit('command', action)"
        >
          {{ action }}
        </button>
      </li>
    </ul>
  </div>
</template>
