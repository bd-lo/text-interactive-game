<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <header class="app-header">
      <h1 class="app-brand"><RouterLink to="/">TEXTGAME</RouterLink></h1>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink to="/cards">人物卡库</RouterLink>
      </nav>
      <span class="app-subtitle">文字冒险</span>
    </header>
    <main id="main-content" class="app-main">
      <RouterView />
    </main>
    <footer class="app-footer">
      <span>文字冒险 · 进度保存在本机后端</span>
    </footer>
  </div>
</template>
