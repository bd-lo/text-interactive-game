<script setup lang="ts">
/**
 * 世界书条目表（H4 + P1）：详情页展示触发词/内容/优先级/常量/启用状态，
 * 以及酒馆级触发语义（概率、包含组、扫描深度、定时效果、position/depth、递归开关）。
 *
 * 只读展示；长内容换行不溢出；触发词按 chips 展示（最多 6 个，其余折叠计数）。
 * P1 字段由服务端从 `raw_json` 还原（`WorldBookEntryResponse`），缺字段按默认值展示。
 */
import { computed } from 'vue'

import type { WorldBookEntry } from '@/api/types'
import {
  labelSelectiveLogic,
  labelTimedEffect,
  labelWorldBookPosition,
} from '@/utils/worldBooks'

const props = defineProps<{
  entries: WorldBookEntry[]
}>()

const KEY_PREVIEW_LIMIT = 6

function keyPreview(keys: string[]): string[] {
  return keys.slice(0, KEY_PREVIEW_LIMIT)
}

function hiddenKeyCount(keys: string[]): number {
  return Math.max(0, keys.length - KEY_PREVIEW_LIMIT)
}

/** 定时效果标签（sticky/cooldown/delay；0 不展示）。 */
function timedTags(entry: WorldBookEntry): string[] {
  return [
    labelTimedEffect(entry.sticky, 'sticky'),
    labelTimedEffect(entry.cooldown, 'cooldown'),
    labelTimedEffect(entry.delay, 'delay'),
  ].filter((tag) => tag !== '')
}

/** 包含组悬停说明：权重/强制优先/组内评分。 */
function groupTitle(entry: WorldBookEntry): string {
  const parts = [`权重 ${entry.group_weight}`]
  if (entry.group_override) {
    parts.push('强制优先')
  }
  if (entry.group_scoring) {
    parts.push('参与组内评分')
  }
  return parts.join(' · ')
}

const enabledCount = computed<number>(() => props.entries.filter((entry) => entry.enabled).length)
</script>

<template>
  <section class="panel world-book-entries" aria-label="世界书条目">
    <h3 class="panel-title">
      条目 <span class="muted">（共 {{ entries.length }} 条 · 启用 {{ enabledCount }}）</span>
    </h3>
    <p v-if="entries.length === 0" class="muted">这本世界书还没有条目。</p>
    <ul v-else class="world-entry-list">
      <li
        v-for="entry in entries"
        :key="entry.entry_uid"
        class="world-entry"
        :class="{ 'world-entry-disabled': !entry.enabled }"
      >
        <div class="world-entry-head">
          <span class="world-entry-uid">#{{ entry.entry_uid }}</span>
          <span v-if="entry.constant" class="world-tag world-tag-constant">常驻</span>
          <span v-if="!entry.enabled" class="world-tag">已停用</span>
          <span
            v-if="entry.selective"
            class="world-tag"
            :title="labelSelectiveLogic(entry.selective_logic)"
          >
            条件触发
          </span>
          <span
            v-if="entry.use_probability"
            class="world-tag"
            :title="`激活概率 ${entry.probability}%：掷骰未命中时本回合不注入`"
          >
            概率 {{ entry.probability }}%
          </span>
          <span v-if="entry.group !== ''" class="world-tag" :title="groupTitle(entry)">
            组「{{ entry.group }}」
          </span>
          <span v-for="tag in timedTags(entry)" :key="tag" class="world-tag world-tag-timed">
            {{ tag }}
          </span>
          <span class="muted world-entry-order">order {{ entry.order }}</span>
        </div>
        <div class="world-entry-keys">
          <span v-if="entry.keys.length === 0" class="muted">（无触发词）</span>
          <span v-for="key in keyPreview(entry.keys)" :key="key" class="world-key">{{ key }}</span>
          <span v-if="hiddenKeyCount(entry.keys) > 0" class="muted">+{{ hiddenKeyCount(entry.keys) }}</span>
        </div>
        <p class="world-entry-content">{{ entry.content }}</p>
        <p v-if="entry.secondary_keys.length > 0" class="muted world-entry-secondary">
          关联词：{{ entry.secondary_keys.join('、') }}
        </p>
        <p class="world-entry-meta">
          <span>{{ labelWorldBookPosition(entry.position) }}</span>
          <span v-if="entry.position === 4">depth {{ entry.depth }}</span>
          <span v-if="entry.scan_depth !== null">扫描深度 {{ entry.scan_depth }}</span>
          <span v-if="entry.case_sensitive">区分大小写</span>
          <span v-if="entry.match_whole_words">整词匹配</span>
          <span v-if="entry.exclude_recursion">不参与递归</span>
          <span v-if="entry.prevent_recursion">阻止向下递归</span>
        </p>
      </li>
    </ul>
  </section>
</template>
