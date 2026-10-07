<template>
  <header class="top-bar">
    <div class="bar-left">
      <RouterLink class="brand" to="/" aria-label="easy-teach 工作台">
        <img class="brand-logo" :src="logoMark" alt="" aria-hidden="true" />
        <span class="brand-name">easy-teach</span>
      </RouterLink>

      <!-- 进入具体教案后收起全局导航，只留项目上下文（门厅 / 工作间分离） -->
      <nav v-if="!inWorkspace" class="lobby-nav" aria-label="主导航">
        <RouterLink
          v-for="item in lobbyNav"
          :key="item.to"
          class="lobby-link"
          :class="{ active: item.active }"
          :to="item.to"
          :aria-label="item.label"
          :aria-current="item.active ? 'page' : undefined"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span class="lobby-label">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <RouterLink v-else class="back-link" to="/" aria-label="返回工作台">
        <el-icon><ArrowLeft /></el-icon>
        <span class="lobby-label">返回工作台</span>
      </RouterLink>
    </div>

    <div class="bar-right">
      <RouterLink v-if="activeProject" class="project-chip" to="/" title="切换项目">
        <span class="chip-label">当前教案</span>
        <strong>{{ activeProject.title }}</strong>
      </RouterLink>

      <el-dropdown trigger="click" @command="handleCommand">
        <button class="user-button" type="button" aria-label="账号菜单">
          <span class="avatar" aria-hidden="true">{{ initial }}</span>
          <span class="user-name">{{ displayName }}</span>
          <el-icon class="chevron"><ArrowDown /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>
              {{ roleLabel }} · {{ displayName }}
            </el-dropdown-item>
            <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowDown, ArrowLeft, Collection, House } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import logoMark from '@/assets/brand/logo-mark.png'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const projectStore = useProjectStore()

// 工作间 = 具体教案的任一环节（需求共创/对话、资料、蓝图、成果、导出）
const inWorkspace = computed(
  () => Boolean(route.meta.projectScoped) || route.name === 'chat',
)

const activeProject = computed(() => projectStore.activeProject)

const lobbyNav = computed(() => [
  {
    to: '/',
    label: '工作台',
    icon: House,
    active: route.name === 'dashboard' || route.name === 'home',
  },
  {
    to: '/knowledge',
    label: '知识库',
    icon: Collection,
    active: route.name === 'knowledge',
  },
])

const displayName = computed(() => auth.displayName || '教师')
const initial = computed(() => String(displayName.value).trim().slice(0, 1) || '师')
const roleLabel = computed(() => (auth.user?.role === 'admin' ? '管理员' : '教师'))

function handleCommand(command) {
  if (command !== 'logout') return
  auth.logout()
  projectStore.clearActiveProject()
  router.replace({ name: 'login' })
}
</script>

<style scoped>
.top-bar {
  flex: 0 0 auto;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
  padding: 0 var(--space-6);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
  /* 顶栏属于外壳，必须在任何页面级全屏背景之上（如新建教案页的极光背景） */
  position: relative;
  z-index: var(--z-sticky);
}

.bar-left,
.bar-right {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.bar-right {
  justify-content: flex-end;
}

/* ── 品牌 ─────────────────────────────────────────────── */
.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  flex: 0 0 auto;
  color: var(--text-primary);
}

.brand-logo {
  width: 30px;
  height: auto;
  flex: 0 0 auto;
}

.brand-name {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  letter-spacing: -0.01em;
}

.brand:hover .brand-name {
  color: var(--text-brand);
}

/* ── 门厅导航 ─────────────────────────────────────────── */
.lobby-nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding-left: var(--space-2);
  border-left: 1px solid var(--border-hairline);
}

.lobby-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-md);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  transition: background-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.lobby-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.lobby-link.active {
  background: var(--bg-active);
  color: var(--text-brand);
}

/* ── 工作间：返回门厅 ─────────────────────────────────── */
.back-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-md);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  transition: background-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.back-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

/* ── 当前教案 ─────────────────────────────────────────── */
.project-chip {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  max-width: 320px;
  height: 36px;
  padding: 0 var(--space-3);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-pill);
  background: var(--bg-surface-sunken);
  transition: border-color var(--duration-fast) var(--ease-standard),
    box-shadow var(--duration-base) var(--ease-standard);
}

.project-chip:hover {
  border-color: var(--border-brand);
  box-shadow: var(--shadow-card);
}

.chip-label {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.project-chip strong {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 用户菜单 ─────────────────────────────────────────── */
.user-button {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 var(--space-2) 0 var(--space-1);
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-standard);
}

.user-button:hover { background: var(--bg-hover); }

.avatar {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-pill);
  background: var(--brand-50);
  color: var(--text-brand);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
}

.user-name {
  color: var(--text-secondary);
  font-size: var(--text-sm);
}

.chevron {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

/* ── 响应式：只保留必要元素 ─────────────────────────────── */
@media (max-width: 1024px) {
  .project-chip { max-width: 200px; }
}

@media (max-width: 768px) {
  .top-bar {
    gap: var(--space-3);
    padding: 0 var(--space-4);
  }

  .brand-name,
  .chip-label { display: none; }

  .project-chip {
    max-width: 140px;
    padding: 0 var(--space-2);
  }

  .user-name { display: none; }

  .lobby-link,
  .back-link {
    min-width: 40px;
    height: 40px;
    justify-content: center;
    padding: 0 var(--space-2);
  }

  /* 视觉上隐藏但保留在无障碍树里：屏幕阅读器仍能读出导航名 */
  .lobby-label {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: calc(var(--space-1) * -1);
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }
}
</style>
