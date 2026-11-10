<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { useCardsStore } from '@/stores/cards'
import { checkCardFileSize } from '@/utils/cards'

const router = useRouter()
const store = useCardsStore()

const jsonText = ref('')
const fileName = ref('')
const localError = ref('')

const validateResult = computed(() => store.validateResult)
const importViolations = computed(() => store.importViolations)
const importWarnings = computed(() => store.importWarnings)

async function onFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  localError.value = ''
  if (file === undefined) {
    return
  }
  const sizeError = checkCardFileSize(file.size)
  if (sizeError !== '') {
    localError.value = sizeError
    return
  }
  fileName.value = file.name
  jsonText.value = await file.text()
}

async function onValidate(): Promise<void> {
  localError.value = ''
  await store.validate(jsonText.value)
}

async function onImport(): Promise<void> {
  localError.value = ''
  const cardId = await store.importText(jsonText.value)
  if (cardId !== null) {
    await router.push({ name: 'card-detail', params: { cardId } })
  }
}

function onClear(): void {
  jsonText.value = ''
  fileName.value = ''
  localError.value = ''
  store.resetImport()
}
</script>

<template>
  <section class="card-import-view">
    <header class="game-head">
      <h2>导入人物卡</h2>
      <p class="game-meta">
        粘贴或选择一张 .json 人物卡（不超过 64KB），校验通过后就能用来开局。
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'cards' }">← 返回卡库</RouterLink>
    </div>

    <ErrorBanner :message="localError" />

    <form class="panel card-import-form" @submit.prevent="onValidate">
      <label class="field">
        <span>选择 JSON 文件（可选；导入时作为 multipart file 上传）</span>
        <input type="file" accept=".json,application/json" @change="onFileChange" />
      </label>
      <p v-if="fileName !== ''" class="muted">已选择：{{ fileName }}</p>
      <label class="field">
        <span>人物卡 JSON（9 字段：schema_version/name/attributes/hp/starting_items/background/appearance/source/generated_by）</span>
        <textarea
          v-model="jsonText"
          class="input card-json-input"
          rows="14"
          placeholder="粘贴 9 字段人物卡 JSON（schema_version/name/attributes/hp/starting_items/background/appearance/source/generated_by）"
        ></textarea>
      </label>
      <div class="game-tools">
        <button class="btn" type="submit" :disabled="store.validateBusy">校验（服务端 validate）</button>
        <button
          class="btn"
          type="button"
          :disabled="store.importBusy"
          @click="onImport"
        >
          {{ store.importBusy ? '导入中…' : '导入并保存' }}
        </button>
        <button class="btn btn-secondary" type="button" @click="onClear">清空</button>
      </div>
    </form>

    <ErrorBanner :message="store.validateError" />
    <template v-if="validateResult !== null">
      <p v-if="validateResult.valid" class="muted" role="status">✅ 服务端校验通过（valid=true），可以导入。</p>
      <div v-else class="panel">
        <h4 class="panel-subtitle">服务端校验未通过（valid=false）</h4>
        <ul class="plain-list">
          <li v-for="(item, index) in validateResult.errors" :key="index">
            {{ item.loc.join('.') || '（根）' }}：{{ item.msg }}
          </li>
        </ul>
      </div>
    </template>

    <ErrorBanner :message="store.importError" />
    <div v-if="importViolations.length > 0" class="panel">
      <h4 class="panel-subtitle">字段级错误</h4>
      <ul class="plain-list">
        <li v-for="(line, index) in importViolations" :key="index">{{ line }}</li>
      </ul>
    </div>
    <div v-if="importWarnings.length > 0" class="panel">
      <h4 class="panel-subtitle">导入告警（服务端规范化说明）</h4>
      <ul class="plain-list">
        <li v-for="warning in importWarnings" :key="warning.code + warning.message">
          {{ warning.code }}：{{ warning.message }}
        </li>
      </ul>
    </div>

    <p class="muted">
      错误映射：413 文件过大 / 422 JSON 或字段不合法 / 403 内容安全（HTML/XSS/URL）/ 409 幂等冲突。
      失败后可手动重试（无自动重试）；成功后跳转详情页。
    </p>
  </section>
</template>
