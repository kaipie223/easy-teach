import { createRouter, createWebHistory } from 'vue-router'
import { getAccessToken, USER_KEY } from '@/api'
import { useProjectStore } from '@/stores/project'

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
    meta: { title: '工作台', teacher: true },
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/home',
    name: 'home',
    meta: { title: '新建项目', teacher: true },
    component: () => import('../views/Home.vue'),
  },
  {
    path: '/chat/:sessionId',
    name: 'chat',
    meta: { title: '课程对话', teacher: true },
    component: () => import('../views/Chat.vue'),
  },
  {
    path: '/requirements',
    name: 'requirements',
    meta: { title: '需求共创', teacher: true, projectScoped: true },
    component: () => import('../views/RequirementsView.vue'),
  },
  {
    path: '/materials',
    name: 'materials',
    meta: { title: '资料中心', teacher: true, projectScoped: true },
    component: () => import('../views/MaterialsView.vue'),
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    meta: { title: '我的知识库', teacher: true },
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
    meta: { title: '教学蓝图', teacher: true, projectScoped: true },
    component: () => import('../views/BlueprintView.vue'),
  },
  {
    path: '/editor',
    name: 'editor',
    meta: { title: '成果编辑', teacher: true, projectScoped: true },
    component: () => import('../views/EditorView.vue'),
  },
  {
    path: '/exports',
    name: 'exports',
    meta: { title: '导出与版本', teacher: true, projectScoped: true },
    component: () => import('../views/ExportsView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

function currentRole() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')?.role || null
  } catch {
    return null
  }
}

router.beforeEach(async (to) => {
  const role = currentRole()
  if (to.meta.public) {
    if (to.name === 'login' && getAccessToken()) {
      return { name: role === 'admin' ? 'admin' : 'dashboard' }
    }
    return true
  }
  if (!getAccessToken()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.admin && role !== 'admin') {
    return { name: 'dashboard' }
  }
  if (to.meta.teacher && role !== 'teacher') {
    return { name: 'admin' }
  }
  if (to.meta.projectScoped) {
    const projectStore = useProjectStore()
    const routeProjectId = typeof to.query.projectId === 'string' ? to.query.projectId : ''
    try {
      if (routeProjectId && projectStore.activeProjectId && routeProjectId !== projectStore.activeProjectId) {
        return { name: 'dashboard', query: { notice: 'select-project' } }
      }
      if (routeProjectId && !projectStore.activeProjectId) {
        await projectStore.selectProjectById(routeProjectId)
      } else {
        await projectStore.ensureActiveProject()
      }
    } catch {
      return { name: 'dashboard', query: { notice: 'project-unavailable' } }
    }
    if (!projectStore.activeProjectId) {
      return { name: 'dashboard', query: { notice: 'select-project' } }
    }
  }
  return true
})

export default router
