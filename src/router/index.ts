import { createRouter, createWebHistory, type RouteRecordRaw, type RouteLocationNormalized } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/home'
  },
  {
    path: '/home',
    name: 'Home',
    component: () => import('@/views/Home.vue'),
    meta: { title: '首页 - AI 智能课件生成系统' }
  },
  {
    path: '/chat/:sessionId',
    name: 'Chat',
    component: () => import('@/views/Chat.vue'),
    meta: { title: '对话主页面' }
  },
  {
    path: '/preview/:taskId',
    name: 'Preview',
    component: () => import('@/views/Preview.vue'),
    meta: { title: '预览与反馈页面' }
  },
  {
    path: '/download/:fileId',
    name: 'Download',
    component: () => import('@/views/Download.vue'),
    meta: { title: '导出下载页面' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 全局后置守卫：设置页面标题
router.afterEach((to: RouteLocationNormalized) => {
  const title = to.meta.title as string
  document.title = title || 'AI 智能课件生成系统'
})

export default router
