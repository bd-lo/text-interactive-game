<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="not-found-view">
    <h2>这一页不存在</h2>
    <p class="muted">链接可能过期了，或者地址敲错了。</p>
    <RouterLink class="btn" :to="{ name: 'home' }">回首页</RouterLink>
  </section>
</template>
