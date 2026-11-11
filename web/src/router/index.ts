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
    // 必须在 `/cards/:cardId` 之前注册，否则会被详情路由吞掉（M6-028）。
    {
      path: '/cards/generate',
      name: 'card-generate',
      component: () => import('@/views/GenerateCardView.vue'),
    },
    {
      path: '/cards/:cardId',
      name: 'card-detail',
      component: () => import('@/views/CardDetailView.vue'),
      props: true,
    },
    { path: '/game/:id', name: 'game', component: () => import('@/views/GameView.vue'), props: true },
    {
      path: '/world-books',
      name: 'world-books',
      component: () => import('@/views/WorldBookLibraryView.vue'),
    },
    {
      path: '/world-books/import',
      name: 'world-book-import',
      component: () => import('@/views/WorldBookImportView.vue'),
    },
    {
      path: '/world-books/:worldBookId',
      name: 'world-book-detail',
      component: () => import('@/views/WorldBookDetailView.vue'),
      props: true,
    },
    // 玩家自上传场景（scenario modules v1）：列表 / 导入 / 详情；`import` 必须在 `:scenarioId` 前。
    {
      path: '/scenarios',
      name: 'scenarios',
      component: () => import('@/views/ScenarioLibraryView.vue'),
    },
    {
      path: '/scenarios/import',
      name: 'scenario-import',
      component: () => import('@/views/ScenarioImportView.vue'),
    },
    {
      path: '/scenarios/:scenarioId',
      name: 'scenario-detail',
      component: () => import('@/views/ScenarioDetailView.vue'),
      props: true,
    },
    // 设置：DeepSeek API Key 输入/测试/清除（LLM 运行期设置）。
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
    },
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
