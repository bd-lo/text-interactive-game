<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 玩家可读的错误标题（如「这句指令没听懂」）；留空只显示正文。 */
    title?: string
    /** 玩家可读的错误正文。 */
    message: string
    /** 技术细节（error_code/HTTP 状态/server 原文），默认折叠不打扰玩家。 */
    detail?: string
    retryable?: boolean
    retryLabel?: string
  }>(),
  { title: '', detail: '', retryable: false, retryLabel: '重试' },
)

const emit = defineEmits<{
  (event: 'retry'): void
}>()
</script>

<template>
  <div v-if="message !== ''" class="error-banner" role="alert">
    <div class="error-body">
      <p v-if="title !== ''" class="error-title">{{ title }}</p>
      <p class="error-text">{{ message }}</p>
      <details v-if="detail !== ''" class="error-details">
        <summary>详情</summary>
        <pre class="error-detail-text">{{ detail }}</pre>
      </details>
    </div>
    <button v-if="retryable" type="button" class="btn btn-small" @click="emit('retry')">
      {{ retryLabel }}
    </button>
  </div>
</template>
