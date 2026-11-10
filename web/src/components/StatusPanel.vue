<script setup lang="ts">
import type { GamePlayerViewResponse } from '@/api/types'
import { labelGameStatus } from '@/utils/labels'

withDefaults(
  defineProps<{
    view: GamePlayerViewResponse | null
    /** 首次加载中（无缓存 view）时的加载态。 */
    loading?: boolean
    /** 加载失败时的错误态（来自 store.error，只读展示）。 */
    error?: string
  }>(),
  { loading: false, error: '' },
)
</script>

<template>
  <section class="panel status-panel" aria-label="玩家状态">
    <h3 class="panel-title">状态</h3>
    <template v-if="error !== ''">
      <p class="status-state error-state" role="status">状态加载失败：{{ error }}</p>
    </template>
    <template v-else-if="view === null && loading">
      <p class="status-state muted" role="status">正在加载状态…</p>
    </template>
    <template v-else-if="view === null">
      <p class="status-state muted" role="status">
        尚无状态数据（GET /api/v1/games/{id}/view 是唯一数据源）。
      </p>
    </template>
    <template v-else>
      <dl class="status-grid">
        <dt>回合数</dt>
        <dd>{{ view.turn }}</dd>
        <dt>HP</dt>
        <dd>{{ view.hp }}/{{ view.max_hp }}</dd>
        <dt>版本</dt>
        <dd>{{ view.version }}</dd>
        <dt>快照版本</dt>
        <dd>{{ view.snapshot_version }}</dd>
        <dt>状态</dt>
        <dd>{{ labelGameStatus(view.status) }}</dd>
        <dt>房间</dt>
        <dd>{{ view.room.title }}（{{ view.room.room_id }}）</dd>
      </dl>
      <p class="status-source muted">数字均来自服务端只读视图（无本地推算）。</p>
      <p class="room-desc">{{ view.room.description }}</p>
      <h4 class="panel-subtitle">出口</h4>
      <ul class="plain-list">
        <li v-for="(target, direction) in view.room.exits" :key="direction">
          {{ direction }} → {{ target }}
        </li>
        <li v-if="Object.keys(view.room.exits).length === 0">（无出口）</li>
      </ul>
      <h4 class="panel-subtitle">可见物品</h4>
      <ul class="plain-list">
        <li v-for="item in view.room.items" :key="item.item_id">{{ item.name }}（{{ item.item_id }}）</li>
        <li v-if="view.room.items.length === 0">（无）</li>
      </ul>
      <h4 class="panel-subtitle">可见 NPC</h4>
      <ul class="plain-list">
        <li v-for="npc in view.room.npcs" :key="npc.npc_id">{{ npc.name }}</li>
        <li v-if="view.room.npcs.length === 0">（无）</li>
      </ul>
      <h4 class="panel-subtitle">行囊</h4>
      <ul class="plain-list">
        <li v-for="item in view.inventory" :key="item.item_id">{{ item.name }}（{{ item.item_id }}）</li>
        <li v-if="view.inventory.length === 0">（空）</li>
      </ul>
    </template>
  </section>
</template>
