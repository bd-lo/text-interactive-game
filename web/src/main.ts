import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { vReveal } from './directives/reveal'
import router from './router'
import { useVisualStore } from './stores/visual'
import './styles/terminal.css'

const pinia = createPinia()
const app = createApp(App).use(pinia).use(router)
app.directive('reveal', vReveal)

// 视觉偏好必须在挂载前初始化：<html data-visual/data-reduced-motion> 决定纯 CSS 降级与特效层懒加载。
useVisualStore(pinia).init()

app.mount('#app')
