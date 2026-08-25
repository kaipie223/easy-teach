import { createRouter, createWebHistory } from 'vue-router'
import { getAccessToken, USER_KEY } from '@/api'

const routes = [
  {
    path: '/login',
    name: 'login',
    meta: { title: '登录', public: true },
    component: () => import('../views/LoginView.vue'),
  },
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
    path: '/knowledge',
    name: 'knowledge',
    meta: { title: '知识库管理', admin: true },
    component: () => import('../views/KnowledgeView.vue'),
  },
  {
    path: '/admin',
    name: 'admin',
    meta: { title: '管理员工作台', admin: true },
    component: () => import('../views/AdminView.vue'),
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

function hasAdminRole() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')?.role === 'admin'
  } catch {
    return false
  }
}

router.beforeEach((to) => {
  if (to.meta.public) {
    if (to.name === 'login' && getAccessToken()) return { name: 'dashboard' }
    return true
  }
  if (!getAccessToken()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.admin && !hasAdminRole()) {
    return { name: 'dashboard' }
  }
  return true
})

export default router
