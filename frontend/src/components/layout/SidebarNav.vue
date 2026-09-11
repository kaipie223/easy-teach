<template>
  <aside class="sidebar-nav">
    <div class="brand">智课共创 TeachMate AI</div>

    <nav class="nav-list" aria-label="主导航">
      <RouterLink
        v-for="item in navItems"
        :key="item.key"
        :to="item.to || route.fullPath"
        :class="['nav-item', { active: item.active, disabled: item.disabled }]"
        :aria-disabled="item.disabled ? 'true' : 'false'"
        @click="handleNavClick($event, item)"
      >
        <el-icon class="nav-icon">
          <component :is="item.icon" />
        </el-icon>
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  Collection,
  Download,
  EditPen,
  FolderOpened,
  House,
  Reading,
  Setting,
  User,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth = useAuthStore()

const teacherNavItems = computed(() => [
  {
    key: 'dashboard',
    label: '工作台',
    to: '/',
    icon: House,
    active: route.name === 'dashboard',
  },
  {
    key: 'requirements',
    label: '需求共创',
    to: '/requirements',
    icon: User,
    active: route.name === 'requirements' || route.name === 'chat',
  },
  {
    key: 'materials',
    label: '资料中心',
    to: '/materials',
    icon: FolderOpened,
    active: route.name === 'materials',
  },
  {
    key: 'knowledge',
    label: '我的知识库',
    to: '/knowledge',
    icon: Collection,
    active: route.name === 'knowledge',
  },
  {
    key: 'blueprint',
    label: '教学蓝图',
    to: '/blueprint',
    icon: Reading,
    active: route.name === 'blueprint',
  },
  {
    key: 'editor',
    label: '成果编辑',
    to: '/editor',
    icon: EditPen,
    active: route.name === 'editor',
  },
  {
    key: 'exports',
    label: '导出与版本',
    to: '/exports',
    icon: Download,
    active: route.name === 'exports',
  },
  {
    key: 'settings',
    label: '设置',
    icon: Setting,
    disabled: true,
  },
])

const navItems = computed(() => (
  auth.user?.role === 'admin'
    ? [
      {
        key: 'admin',
        label: '管理员工作台',
        to: '/admin',
        icon: UserFilled,
        active: route.name === 'admin',
      },
    ]
    : teacherNavItems.value
))

function handleNavClick(event, item) {
  if (item.disabled) {
    event.preventDefault()
  }
}
</script>

<style scoped>
.sidebar-nav {
  width: 228px;
  min-width: 228px;
  border-right: 1px solid #e6eaf0;
  background: #ffffff;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.brand {
  height: 64px;
  display: flex;
  align-items: center;
  padding: 0 24px;
  border-bottom: 1px solid #e6eaf0;
  font-size: 16px;
  font-weight: 700;
  color: #111827;
  white-space: nowrap;
}

.nav-list {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 20px 16px;
  overflow-y: auto;
}

.nav-item {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px;
  border-radius: 8px;
  color: #374151;
  text-decoration: none;
  font-size: 15px;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.nav-item:hover {
  background: #f3f7ff;
  color: #0f56d9;
}

.nav-item.active {
  background: #1463ff;
  color: #ffffff;
  box-shadow: 0 8px 18px rgba(20, 99, 255, 0.2);
}

.nav-item.disabled {
  cursor: default;
  color: #6b7280;
}

.nav-item.disabled:hover {
  background: transparent;
  color: #6b7280;
}

.nav-icon {
  font-size: 20px;
  flex: 0 0 auto;
}

@media (max-width: 900px) {
  .sidebar-nav {
    width: 72px;
    min-width: 72px;
  }

  .brand {
    justify-content: center;
    padding: 0;
    font-size: 0;
  }

  .brand::before {
    content: "TM";
    font-size: 15px;
    font-weight: 800;
    color: #1463ff;
  }

  .nav-list {
    padding: 16px 10px;
  }

  .nav-item {
    justify-content: center;
    padding: 0;
  }

  .nav-item span {
    display: none;
  }
}
</style>
