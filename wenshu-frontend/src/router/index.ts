import { createRouter, createWebHistory } from 'vue-router'

import { getStoredUser, getToken } from '@/lib/authToken'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    { path: '/', redirect: '/sources' },
    {
      path: '/db',
      name: 'db-chat',
      component: () => import('@/views/DbChatView.vue'),
    },
    {
      path: '/sources',
      name: 'source-list',
      component: () => import('@/views/DatasourceListView.vue'),
    },
    {
      path: '/sources/:id/meta',
      name: 'source-meta',
      component: () => import('@/views/DatasourceMetaView.vue'),
      meta: { requiresAdmin: true },   // 编辑元数据仅管理员可进(后端写接口也已 require_admin)
    },
    {
      path: '/datasets',
      name: 'dataset-list',
      component: () => import('@/views/DatasetListView.vue'),
    },
    {
      path: '/datasets/:id/chat',
      name: 'dataset-chat',
      component: () => import('@/views/DatasetChatView.vue'),
    },
    {
      path: '/datasets/:id/edit',
      name: 'dataset-edit',
      component: () => import('@/views/DatasetEditView.vue'),
    },
    {
      // 归因分析独立页:从问数/数据集结果卡发起,window.open 新标签页打开,
      // 数据经 localStorage 交接(?id= 是交接条目的 key,见 lib/attribution-handoff.ts)
      path: '/attribution',
      name: 'attribution',
      component: () => import('@/views/AttributionView.vue'),
    },
    // 通配兜底:任何匹配不上的路径都落到 404 页(public,登录与否都能看到)
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { public: true },
    },
  ],
})

// 全局登录守卫:未登录访问受保护页 → 跳登录(带 redirect 回跳);已登录再进 /login → 回首页。
// 这里直接读 localStorage 里的 token(不依赖 Pinia),避免守卫早于 store 初始化的时序问题。
router.beforeEach((to) => {
  const authed = !!getToken()
  if (!to.meta.public && !authed) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && authed) {
    return { path: '/db' }
  }
  // 管理员专属路由:非管理员(含本地登录态无 role)挡回数据源列表。
  // 仅前端体验防护;后端对应写接口已 require_admin 兜底。
  if (to.meta.requiresAdmin && getStoredUser()?.role !== 'admin') {
    return { path: '/sources' }
  }
  return true
})

export default router
