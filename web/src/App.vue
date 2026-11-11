<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

import ThemeToggle from '@/components/ThemeToggle.vue'
import VisualSettings from '@/components/VisualSettings.vue'
import { useVisualStore } from '@/stores/visual'

const visual = useVisualStore()

// 重特效层懒加载：关闭特效/低功耗时不下载该 chunk；组件内部再按档位降级。
const MoteField = defineAsyncComponent(() => import('@/components/MoteField.vue'))
const AtmosphereOverlay = defineAsyncComponent(
  () => import('@/components/AtmosphereOverlay.vue'),
)
// 启动器控制面懒加载：把 API client/退出状态机留在独立 chunk，不拖慢首屏入口 bundle
// （组件挂载后只读一次 `/app/status`，非启动器模式静默隐藏）。
const QuitGameControl = defineAsyncComponent(() => import('@/components/QuitGameControl.vue'))
</script>

<template>
  <div class="app-shell">
    <AtmosphereOverlay v-if="visual.atmosphereEnabled" />
    <MoteField v-if="visual.particlesEnabled" />
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <header class="app-header">
      <h1 class="app-brand">
        <RouterLink to="/">
          <span class="sr-only">TEXTGAME 首页</span>
          <span class="app-wordmark" aria-hidden="true">TEXTGAME</span>
        </RouterLink>
      </h1>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink to="/cards">人物卡库</RouterLink>
        <RouterLink to="/world-books">世界书</RouterLink>
        <RouterLink to="/scenarios">场景</RouterLink>
        <RouterLink to="/settings">设置</RouterLink>
      </nav>
      <div class="app-controls">
        <ThemeToggle />
        <VisualSettings />
        <QuitGameControl />
      </div>
    </header>
    <main id="main-content" class="app-main">
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>
    <footer class="app-footer">
      <span>文字冒险 · 进度保存在本机后端</span>
    </footer>
  </div>
</template>
