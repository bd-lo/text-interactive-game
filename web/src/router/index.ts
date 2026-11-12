import { createRouter, createWebHistory } from 'vue-router'

import { focusMainContent } from '@/utils/a11y'

// 路由级懒加载（R2 包 C）：对局页不必下载卡库/回放代码，首屏 JS 更小。
// `meta.title` 用于路由切换后更新 `document.title`（WCAG 2.4.2「页面有标题」的 SPA 落地）。
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      meta: { title: '首页' },
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/cards',
      name: 'cards',
      meta: { title: '人物卡库' },
      component: () => import('@/views/CardLibraryView.vue'),
    },
    {
      path: '/cards/import',
      name: 'card-import',
      meta: { title: '导入人物卡' },
      component: () => import('@/views/CardImportView.vue'),
    },
    // 必须在 `/cards/:cardId` 之前注册，否则会被详情路由吞掉（M6-028）。
    {
      path: '/cards/generate',
      name: 'card-generate',
      meta: { title: '生成人物卡' },
      component: () => import('@/views/GenerateCardView.vue'),
    },
    {
      path: '/cards/:cardId',
      name: 'card-detail',
      meta: { title: '人物卡详情' },
      component: () => import('@/views/CardDetailView.vue'),
      props: true,
    },
    {
      path: '/game/:id',
      name: 'game',
      meta: { title: '对局' },
      component: () => import('@/views/GameView.vue'),
      props: true,
    },
    {
      path: '/world-books',
      name: 'world-books',
      meta: { title: '世界书' },
      component: () => import('@/views/WorldBookLibraryView.vue'),
    },
    {
      path: '/world-books/import',
      name: 'world-book-import',
      meta: { title: '导入世界书' },
      component: () => import('@/views/WorldBookImportView.vue'),
    },
    {
      path: '/world-books/:worldBookId',
      name: 'world-book-detail',
      meta: { title: '世界书详情' },
      component: () => import('@/views/WorldBookDetailView.vue'),
      props: true,
    },
    // 玩家自上传场景（scenario modules v1）：列表 / 导入 / 详情；`import` 必须在 `:scenarioId` 前。
    {
      path: '/scenarios',
      name: 'scenarios',
      meta: { title: '场景库' },
      component: () => import('@/views/ScenarioLibraryView.vue'),
    },
    {
      path: '/scenarios/import',
      name: 'scenario-import',
      meta: { title: '导入场景' },
      component: () => import('@/views/ScenarioImportView.vue'),
    },
    // N1-031 场景编辑器：必须在 `/scenarios/:scenarioId` 之前注册，否则会被详情路由吞掉。
    {
      path: '/scenarios/editor',
      name: 'scenario-editor',
      meta: { title: '场景编辑器' },
      component: () => import('@/views/ScenarioEditorView.vue'),
    },
    {
      path: '/scenarios/:scenarioId',
      name: 'scenario-detail',
      meta: { title: '场景详情' },
      component: () => import('@/views/ScenarioDetailView.vue'),
      props: true,
    },
    // 设置：DeepSeek API Key 输入/测试/清除（LLM 运行期设置）。
    {
      path: '/settings',
      name: 'settings',
      meta: { title: '设置' },
      component: () => import('@/views/SettingsView.vue'),
    },
    {
      path: '/game/:id/replay',
      name: 'replay',
      meta: { title: '回放' },
      component: () => import('@/views/ReplayView.vue'),
      props: true,
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      meta: { title: '页面不存在' },
      component: () => import('@/views/NotFoundView.vue'),
    },
  ],
})

// 路由切换后：更新标题 + 把焦点交给主区域（跳过导航/页脚），读屏与键盘用户从新页面开头继续。
// 首次加载（from 为 START_LOCATION）不抢焦点：此时用户可能正用 Tab 从地址栏进入，跳过链接必须仍是第一站。
router.afterEach((to, from) => {
  if (from.name === undefined) {
    return
  }
  const title = to.meta.title
  if (typeof title === 'string' && title !== '') {
    document.title = `${title} · TEXTGAME`
  }
  focusMainContent()
})

export default router
