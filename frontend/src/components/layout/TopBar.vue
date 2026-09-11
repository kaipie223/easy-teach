<template>
  <header class="top-bar">
    <div class="breadcrumb">
      <el-icon class="home-icon"><House /></el-icon>
      <span class="slash">/</span>
      <span>{{ currentTitle }}</span>
      <template v-if="activeProjectName">
        <span class="slash">/</span>
        <span class="project-context" :title="activeProjectName">{{ activeProjectName }}</span>
      </template>
    </div>

    <div class="top-actions">
      <div class="user-menu">
        <el-avatar :size="32">
          <el-icon><UserFilled /></el-icon>
        </el-avatar>
        <span>{{ auth.displayName }}</span>
        <el-button class="logout-button" text @click="logout">退出</el-button>
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { House, UserFilled } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const projectStore = useProjectStore()
const currentTitle = computed(() => route.meta.title || '工作台')
const activeProjectName = computed(() => projectStore.activeProject?.title || '')

function logout() {
  auth.logout()
  router.replace({ name: 'login' })
}
</script>

<style scoped>
.top-bar {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 0 28px;
  border-bottom: 1px solid #e6eaf0;
  background: rgba(255, 255, 255, 0.96);
}

.breadcrumb {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  color: #374151;
  font-size: 15px;
}

.home-icon {
  font-size: 18px;
}

.slash {
  color: #9ca3af;
}

.project-context {
  max-width: 240px;
  overflow: hidden;
  color: #1463ff;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 0 0 auto;
}

.user-menu {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #111827;
  font-size: 14px;
  white-space: nowrap;
}

.arrow {
  color: #6b7280;
  font-size: 14px;
}

.logout-button {
  color: #64748b;
  padding: 0 4px;
}

@media (max-width: 720px) {
  .top-bar {
    padding: 0 16px;
  }

  .user-menu span,
  .arrow {
    display: none;
  }
}
</style>
