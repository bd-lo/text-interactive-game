<script setup lang="ts">
import type { GamePlayerViewResponse } from '@/api/types'

defineProps<{
  view: GamePlayerViewResponse | null
}>()
</script>

<template>
  <section class="panel status-panel" aria-label="玩家状态">
    <h3 class="panel-title">状态</h3>
    <template v-if="view !== null">
      <dl class="status-grid">
        <dt>HP</dt>
        <dd>{{ view.hp }}/{{ view.max_hp }}</dd>
        <dt>回合</dt>
        <dd>{{ view.turn }}</dd>
        <dt>版本</dt>
        <dd>{{ view.version }}（快照 {{ view.snapshot_version }}）</dd>
        <dt>房间</dt>
        <dd>{{ view.room.title }}（{{ view.room.room_id }}）</dd>
      </dl>
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
    <p v-else class="muted">状态未加载（GET /api/v1/games/{id}/view 是唯一数据源）。</p>
  </section>
</template>
