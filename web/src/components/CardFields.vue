<script setup lang="ts">
import { computed } from 'vue'

import type { CharacterCard } from '@/api/types'
import { ATTRIBUTE_LABELS, labelCardSource } from '@/utils/labels'

const props = defineProps<{
  card: CharacterCard
}>()

const attributeEntries = computed<Array<{ key: string; label: string; value: number }>>(() =>
  Object.entries(props.card.attributes).map(([key, value]) => ({
    key,
    label: ATTRIBUTE_LABELS[key as keyof typeof ATTRIBUTE_LABELS] ?? key,
    value,
  })),
)
</script>

<template>
  <!-- 9 字段只读展示（第 5 章 §5.7 / 第 23 章 §23.5）；全部文本插值，无 HTML 注入。 -->
  <section class="panel card-fields" aria-label="人物卡 9 字段">
    <h3 class="panel-title">{{ card.name }}</h3>
    <dl class="status-grid">
      <dt>名称</dt>
      <dd>{{ card.name }}</dd>
      <dt>HP</dt>
      <dd>{{ card.hp }} / {{ card.attributes.max_hp }}</dd>
      <dt>schema 版本</dt>
      <dd>{{ card.schema_version }}</dd>
      <dt>来源</dt>
      <dd>{{ labelCardSource(card.source) }}</dd>
      <dt>生成者</dt>
      <dd>{{ card.generated_by === null || card.generated_by === '' ? '（无）' : card.generated_by }}</dd>
      <dt>初始物品</dt>
      <dd>{{ card.starting_items.length === 0 ? '（无）' : card.starting_items.join('、') }}</dd>
      <dt>属性</dt>
      <dd class="attr-list">
        <span v-for="entry in attributeEntries" :key="entry.key" class="attr-chip">
          {{ entry.label }} {{ entry.value }}
        </span>
      </dd>
      <dt>背景</dt>
      <dd class="pre-text">{{ card.background }}</dd>
      <dt>外貌</dt>
      <dd class="pre-text">{{ card.appearance === null ? '（无）' : card.appearance }}</dd>
    </dl>
  </section>
</template>
