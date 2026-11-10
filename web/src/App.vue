<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <RouterLink class="app-brand" to="/">TEXTGAME</RouterLink>
      <nav class="app-nav">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink to="/cards">人物卡库</RouterLink>
      </nav>
      <span class="app-subtitle">2G 文字 MUD · 离线确定性叙事</span>
    </header>
    <main class="app-main">
      <RouterView />
    </main>
    <footer class="app-footer">
      <span>Node 仅构建期 · 运行期由 FastAPI 托管</span>
    </footer>
  </div>
</template>
