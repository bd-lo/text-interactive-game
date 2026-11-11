import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { vReveal } from './directives/reveal'
import router from './router'
import { useThemeStore } from './stores/theme'
import { useVisualStore } from './stores/visual'
import './styles/theme.css'

const pinia = createPinia()
const app = createApp(App).use(pinia).use(router)
app.directive('reveal', vReveal)

// 主题与视觉偏好必须在挂载前初始化：`<html data-theme/data-visual/data-reduced-motion>`
// 决定纯 CSS 皮肤与特效降级，晚一步就会出现色彩跳变。
useThemeStore(pinia).init()
useVisualStore(pinia).init()

app.mount('#app')
