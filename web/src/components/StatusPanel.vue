<script setup lang="ts">
import { computed } from 'vue'

import type { GamePlayerViewResponse } from '@/api/types'
import ActionChips from '@/components/ActionChips.vue'
import { labelGameStatus } from '@/utils/labels'
import { exitSuggestions, exitTitles, itemSuggestions, npcSuggestions } from '@/utils/suggestions'

const props = withDefaults(
  defineProps<{
    view: GamePlayerViewResponse | null
    /** 首次加载中（无缓存 view）时的加载态。 */
    loading?: boolean
    /** 结局后禁止再点出口/物品/NPC 指令。 */
    disabled?: boolean
  }>(),
  { loading: false, disabled: false },
)

const emit = defineEmits<{
  (event: 'command', text: string): void
}>()

// 出口/物品/NPC 只暴露玩家可读名称与可点击指令，**不展示内部 ID**（room_/item_/npc_）。
const exitCommands = computed<string[]>(() =>
  exitSuggestions(props.view).map((suggestion) => suggestion.command),
)
const itemCommands = computed<string[]>(() =>
  itemSuggestions(props.view).map((suggestion) => suggestion.command),
)
const npcCommands = computed<string[]>(() =>
  npcSuggestions(props.view).map((suggestion) => suggestion.command),
)
/** 出口的悬停/读屏说明：通往哪个房间（用 exit_names，不暴露内部 ID）。 */
const exitChipTitles = computed<Record<string, string>>(() => exitTitles(props.view))

function onCommand(text: string): void {
  emit('command', text)
}
</script>

<template>
  <section class="panel status-panel" aria-label="玩家状态">
    <h3 class="panel-title">状态</h3>
    <template v-if="view === null && loading">
      <p class="status-state muted" role="status">正在加载状态…</p>
    </template>
    <template v-else-if="view === null">
      <p class="status-state muted" role="status">对局状态尚未载入。</p>
    </template>
    <template v-else>
      <dl class="status-grid">
        <dt>回合</dt>
        <dd>第 {{ view.turn }} 回合</dd>
        <dt>HP</dt>
        <dd>{{ view.hp }}/{{ view.max_hp }}</dd>
        <dt>状态</dt>
        <dd>{{ labelGameStatus(view.status) }}</dd>
      </dl>
      <p class="room-desc">
        <strong>{{ view.room.title }}</strong>
        {{ view.room.description }}
      </p>

      <h4 class="panel-subtitle">出口</h4>
      <ActionChips
        v-if="exitCommands.length > 0"
        :actions="exitCommands"
        :titles="exitChipTitles"
        :disabled="disabled"
        @command="onCommand"
      />
      <p v-else class="muted">（没有出口）</p>

      <h4 class="panel-subtitle">这里的东西</h4>
      <ActionChips
        v-if="itemCommands.length > 0"
        :actions="itemCommands"
        :disabled="disabled"
        @command="onCommand"
      />
      <p v-else class="muted">（没有可拿的东西）</p>

      <h4 class="panel-subtitle">这里的人</h4>
      <ActionChips
        v-if="npcCommands.length > 0"
        :actions="npcCommands"
        :disabled="disabled"
        @command="onCommand"
      />
      <p v-else class="muted">（没有别人）</p>

      <h4 class="panel-subtitle">行囊</h4>
      <ul class="plain-list inventory-list">
        <li v-for="item in view.inventory" :key="item.item_id">{{ item.name }} ×{{ item.qty }}</li>
        <li v-if="view.inventory.length === 0">（空）</li>
      </ul>
    </template>
  </section>
</template>
