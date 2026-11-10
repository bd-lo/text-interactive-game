<script setup lang="ts">
/**
 * 对局内「世界设定」面板（H4）：显示本局绑定的世界书 + 本回合激活条目。
 *
 * - 无世界书（`worldBook === null`）时父层不渲染；
 * - 只读：`view.world_book` / `view.lore.active_entries` 均来自服务端只读视图；
 * - 长条目滚动展示，`aria-live="polite"` 在激活集合变化时播报条数。
 */
import { computed } from 'vue'

import type { LoreView, WorldBookRef } from '@/api/types'

const props = defineProps<{
  worldBook: WorldBookRef | null
  lore: LoreView | null
}>()

const activeEntries = computed(() => props.lore?.active_entries ?? [])
</script>

<template>
  <section v-if="worldBook !== null" class="panel world-lore-panel" aria-label="世界设定">
    <h3 class="panel-title">世界设定</h3>
    <p class="world-lore-summary">
      《{{ worldBook.name }}》
      <span class="muted">· 共 {{ worldBook.entry_count }} 条</span>
    </p>
    <div class="world-lore-entries" aria-live="polite">
      <p v-if="activeEntries.length === 0" class="muted">本回合没有触发新的设定条目。</p>
      <ul v-else class="world-lore-list">
        <li v-for="entry in activeEntries" :key="entry.entry_uid" class="world-lore-entry">
          {{ entry.content }}
        </li>
      </ul>
    </div>
  </section>
</template>
