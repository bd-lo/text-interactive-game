<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import ErrorBanner from '@/components/ErrorBanner.vue'
import { useWorldBooksStore } from '@/stores/worldBooks'
import {
  checkWorldBookFileSize,
  labelDetectedFormat,
  WORLD_BOOK_MAX_BYTES,
} from '@/utils/worldBooks'

const router = useRouter()
const store = useWorldBooksStore()

const jsonText = ref('')
const fileName = ref('')
const localError = ref('')
const dragging = ref(false)

const validateResult = computed(() => store.validateResult)
const importWarnings = computed(() => store.importWarnings)
const importError = computed(() => store.importError)

async function readFile(file: File): Promise<void> {
  localError.value = ''
  const sizeError = checkWorldBookFileSize(file.size)
  if (sizeError !== '') {
    localError.value = sizeError
    return
  }
  fileName.value = file.name
  jsonText.value = await file.text()
}

async function onFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file !== undefined) {
    await readFile(file)
  }
}

async function onDrop(event: DragEvent): Promise<void> {
  dragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file !== undefined) {
    await readFile(file)
  }
}

async function onValidate(): Promise<void> {
  localError.value = ''
  await store.validate(jsonText.value)
}

async function onImport(): Promise<void> {
  localError.value = ''
  const worldBookId = await store.importText(jsonText.value, {
    filename: fileName.value === '' ? undefined : fileName.value,
  })
  if (worldBookId !== null) {
    await router.push({ name: 'world-book-detail', params: { worldBookId } })
  }
}

function onClear(): void {
  jsonText.value = ''
  fileName.value = ''
  localError.value = ''
  store.resetImport()
  store.resetValidate()
}
</script>

<template>
  <section class="world-book-import-view">
    <header class="game-head">
      <h2 class="world-books-title">导入世界书</h2>
      <p class="game-meta">
        支持酒馆世界书（world_info）、角色卡内嵌 character_book 与简化 JSON；单个文件不超过 256KB
        （{{ WORLD_BOOK_MAX_BYTES }} 字节）。
      </p>
    </header>

    <div class="game-tools">
      <RouterLink class="btn btn-small btn-secondary" :to="{ name: 'world-books' }">
        ← 返回世界书
      </RouterLink>
    </div>

    <ErrorBanner :message="localError" />

    <form class="panel world-book-import-form" @submit.prevent="onValidate">
      <label class="field">
        <span>选择 JSON 文件</span>
        <input type="file" accept=".json,application/json" @change="onFileChange" />
        <span class="field-hint muted">也可以把文件直接拖进下面的文本框区域。</span>
      </label>
      <p v-if="fileName !== ''" class="muted">已选择：{{ fileName }}</p>
      <div
        class="world-book-dropzone"
        :class="{ 'world-book-dropzone-active': dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <label class="field">
          <span>或粘贴世界书 JSON</span>
          <textarea
            v-model="jsonText"
            class="input card-json-input"
            rows="14"
            placeholder='粘贴 JSON，例如 {"name":"余烬哨站","entries":[{"keys":["哨站"],"content":"..."}]}'
          ></textarea>
        </label>
      </div>
      <div class="game-tools">
        <button class="btn" type="submit" :disabled="store.validateBusy">
          {{ store.validateBusy ? '校验中…' : '校验（只看不存）' }}
        </button>
        <button class="btn" type="button" :disabled="store.importBusy" @click="onImport">
          {{ store.importBusy ? '导入中…' : '导入并保存' }}
        </button>
        <button class="btn btn-secondary" type="button" @click="onClear">清空</button>
      </div>
    </form>

    <ErrorBanner :message="store.validateError" />
    <template v-if="validateResult !== null">
      <section v-if="validateResult.valid" class="panel world-book-preview-panel">
        <h3 class="panel-title">格式识别通过</h3>
        <dl class="status-grid">
          <dt>名称</dt>
          <dd>{{ validateResult.preview?.name === '' ? '（未命名）' : validateResult.preview?.name }}</dd>
          <dt>条目数</dt>
          <dd>{{ validateResult.preview?.entry_count ?? 0 }} 条</dd>
          <dt>识别格式</dt>
          <dd>{{ labelDetectedFormat(validateResult.preview?.detected_format) }}</dd>
        </dl>
      </section>
      <section v-else class="panel world-book-preview-panel">
        <h3 class="panel-title">校验未通过</h3>
        <ul class="plain-list">
          <li v-for="(item, index) in validateResult.errors ?? []" :key="index">
            {{ item.loc.join('.') || '（根）' }}：{{ item.msg }}
          </li>
        </ul>
      </section>
      <section v-if="(validateResult.warnings ?? []).length > 0" class="panel world-book-preview-panel">
        <h3 class="panel-title">提示</h3>
        <ul class="plain-list">
          <li v-for="item in validateResult.warnings ?? []" :key="item.code + item.message">
            {{ item.message }}
          </li>
        </ul>
      </section>
    </template>

    <ErrorBanner
      :title="importError?.title"
      :message="importError?.message ?? ''"
      :detail="importError?.detail"
    />
    <section v-if="importWarnings.length > 0" class="panel">
      <h3 class="panel-title">导入提示</h3>
      <ul class="plain-list">
        <li v-for="item in importWarnings" :key="item.code + item.message">{{ item.message }}</li>
      </ul>
    </section>
  </section>
</template>
