import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('../views/ChatView.vue'),
  },
  {
    path: '/preview/:taskId',
    name: 'preview',
    component: () => import('../views/PreviewView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
