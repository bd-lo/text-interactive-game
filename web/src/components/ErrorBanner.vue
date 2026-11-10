<script setup lang="ts">
defineProps<{
  message: string
  retryable?: boolean
}>()

const emit = defineEmits<{
  (event: 'retry'): void
}>()
</script>

<template>
  <div v-if="message !== ''" class="error-banner" role="alert">
    <span class="error-text">{{ message }}</span>
    <button v-if="retryable === true" type="button" class="btn btn-small" @click="emit('retry')">
      重试（手动）
    </button>
  </div>
</template>
