import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/db' },
    {
      path: '/db',
      name: 'db-chat',
      component: () => import('@/views/DbChatView.vue'),
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
  ],
})

export default router
