import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    meta: { title: '工作台' },
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/home',
    name: 'home',
    meta: { title: '开始新课' },
    component: () => import('../views/Home.vue'),
  },
  {
    path: '/chat/:sessionId',
    name: 'chat',
    meta: { title: '课程对话' },
    component: () => import('../views/Chat.vue'),
  },
  {
    path: '/requirements',
    name: 'requirements',
    meta: { title: '需求共创' },
    component: () => import('../views/RequirementsView.vue'),
  },
  {
    path: '/materials',
    name: 'materials',
    meta: { title: '资料中心' },
    component: () => import('../views/MaterialsView.vue'),
  },
  {
    path: '/blueprint',
    name: 'blueprint',
    meta: { title: '教学蓝图' },
    component: () => import('../views/BlueprintView.vue'),
  },
  {
    path: '/editor',
    name: 'editor',
    meta: { title: '成果编辑' },
    component: () => import('../views/EditorView.vue'),
  },
  {
    path: '/exports',
    name: 'exports',
    meta: { title: '导出与版本' },
    component: () => import('../views/ExportsView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
