import { createRouter, createWebHistory } from 'vue-router'

import CardDetailView from '@/views/CardDetailView.vue'
import CardImportView from '@/views/CardImportView.vue'
import CardLibraryView from '@/views/CardLibraryView.vue'
import GameView from '@/views/GameView.vue'
import HomeView from '@/views/HomeView.vue'
import ReplayView from '@/views/ReplayView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/cards', name: 'cards', component: CardLibraryView },
    { path: '/cards/import', name: 'card-import', component: CardImportView },
    { path: '/cards/:cardId', name: 'card-detail', component: CardDetailView, props: true },
    { path: '/game/:id', name: 'game', component: GameView, props: true },
    { path: '/game/:id/replay', name: 'replay', component: ReplayView, props: true },
  ],
})

export default router
