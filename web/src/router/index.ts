import { createRouter, createWebHistory } from 'vue-router'

// 路由级懒加载（R2 包 C）：对局页不必下载卡库/回放代码，首屏 JS 更小。
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/cards', name: 'cards', component: () => import('@/views/CardLibraryView.vue') },
    {
      path: '/cards/import',
      name: 'card-import',
      component: () => import('@/views/CardImportView.vue'),
    },
    {
      path: '/cards/:cardId',
      name: 'card-detail',
      component: () => import('@/views/CardDetailView.vue'),
      props: true,
    },
    { path: '/game/:id', name: 'game', component: () => import('@/views/GameView.vue'), props: true },
    {
      path: '/game/:id/replay',
      name: 'replay',
      component: () => import('@/views/ReplayView.vue'),
      props: true,
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
    },
  ],
})

export default router
