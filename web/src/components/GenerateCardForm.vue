<script setup lang="ts">
/**
 * 生成人物卡表单（M6-028）：brief 码点计数 ≤1000、可选 options.tone、空/超限禁用提交。
 * 只做输入与预检；提交动作交给父视图（`GenerateCardView` → store.generate()）。
 */
import { computed } from 'vue'

import { useCardGenerateStore } from '@/stores/cardGenerate'
import { GENERATE_BRIEF_MAX_CHARS, GENERATE_TONE_OPTIONS } from '@/utils/cardGenerate'

const emit = defineEmits<{
  (event: 'submit'): void
}>()

const store = useCardGenerateStore()
const toneOptions = GENERATE_TONE_OPTIONS
const counterClass = computed<string>(() => (store.briefOverLimit ? 'counter counter-over' : 'counter'))

function onSubmit(): void {
  emit('submit')
}

/** 输入同步进 store（去抖/自动请求均无：只在点「生成」时发请求）。 */
function onBriefInput(event: Event): void {
  store.brief = (event.target as HTMLTextAreaElement).value
}
</script>

<template>
  <form class="panel generate-form" aria-label="生成人物卡表单" @submit.prevent="onSubmit">
    <label class="field">
      <span>人物设定（brief，≤{{ GENERATE_BRIEF_MAX_CHARS }} 字）</span>
      <textarea
        :value="store.brief"
        data-testid="brief-input"
        class="input generate-brief"
        rows="5"
        placeholder="例如：一个在旧矿镇长大的拾荒者，沉默但记性极好，随身带着一盏旧提灯。"
        :disabled="store.busy"
        @input="onBriefInput"
      ></textarea>
      <span class="field-hint muted">
        <span :class="counterClass" data-testid="brief-counter">{{ store.briefChars }}/{{ GENERATE_BRIEF_MAX_CHARS }}</span>
        · 按字符（码点）计数；空或超限时不能生成。
      </span>
    </label>

    <fieldset class="field tone-field">
      <legend>语气（可选，仅用于生成参考/请求去重）</legend>
      <span class="tone-options">
        <label v-for="option in toneOptions" :key="option.value" class="tone-option">
          <input
            v-model="store.tone"
            type="radio"
            name="card-tone"
            :value="option.value"
            :disabled="store.busy"
          />
          {{ option.label }}
        </label>
      </span>
    </fieldset>

    <div class="game-tools">
      <button
        class="btn"
        type="submit"
        data-testid="generate-button"
        :disabled="!store.canGenerate"
      >
        {{ store.busy ? '生成中…' : '生成人物卡' }}
      </button>
      <span class="muted">手动重试复用同一请求标识；失败不会自动重试。</span>
    </div>
  </form>
</template>
