import { createRouter, createWebHistory } from 'vue-router'

import GameView from '@/views/GameView.vue'
import HomeView from '@/views/HomeView.vue'
import ReplayView from '@/views/ReplayView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/game/:id', name: 'game', component: GameView, props: true },
    { path: '/game/:id/replay', name: 'replay', component: ReplayView, props: true },
  ],
})

export default router
