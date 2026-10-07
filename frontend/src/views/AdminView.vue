<template>
  <div class="page-stack admin-page">
    <AppPageHeader
      eyebrow="平台管理 · 只读目录"
      title="管理员工作台"
      subtitle="查看平台用户的账号、角色与启用状态，数据来自当前服务端数据库。"
    >
      <template #actions>
        <el-tag type="warning" effect="plain">管理员权限</el-tag>
        <el-button :loading="loading" @click="loadUsers">
          <el-icon><Refresh /></el-icon>
          刷新用户
        </el-button>
      </template>
    </AppPageHeader>

    <el-alert
      v-if="pageError"
      :title="pageError"
      type="error"
      show-icon
      :closable="false"
    >
      <template #default>
        <el-button size="small" type="primary" @click="loadUsers">重试</el-button>
      </template>
    </el-alert>

    <section class="stat-grid admin-stat-grid" aria-label="用户概览">
      <article class="metric-card">
        <span class="metric-icon"><el-icon><UserFilled /></el-icon></span>
        <div><span>用户总数</span><strong>{{ users.length }}</strong></div>
      </article>
      <article class="metric-card">
        <span class="metric-icon admin-icon-green"><el-icon><Avatar /></el-icon></span>
        <div><span>教师账号</span><strong>{{ teacherCount }}</strong></div>
      </article>
      <article class="metric-card">
        <span class="metric-icon admin-icon-amber"><el-icon><Key /></el-icon></span>
        <div><span>管理员账号</span><strong>{{ adminCount }}</strong></div>
      </article>
      <article class="metric-card">
        <span class="metric-icon admin-icon-slate"><el-icon><CircleCheck /></el-icon></span>
        <div><span>已启用账号</span><strong>{{ activeCount }}</strong></div>
      </article>
    </section>

    <section class="section-card">
      <div class="section-header directory-header">
        <div>
          <h2>用户目录</h2>
          <p>当前显示 {{ filteredUsers.length }} / {{ users.length }} 个账号；本页面暂不提供账号编辑和删除操作。</p>
        </div>
        <span v-if="lastLoadedAt" class="directory-meta">更新于 {{ formatDate(lastLoadedAt) }}</span>
      </div>

      <div class="directory-toolbar" aria-label="用户目录筛选">
        <div class="filter-field filter-search">
          <label for="admin-user-search">搜索用户</label>
          <el-input
            id="admin-user-search"
            v-model="searchQuery"
            clearable
            placeholder="按邮箱、姓名或用户 ID 搜索"
          />
        </div>
        <div class="filter-field">
          <label for="admin-role-filter">角色</label>
          <el-select id="admin-role-filter" v-model="roleFilter" class="filter-control">
            <el-option label="全部角色" value="all" />
            <el-option label="教师" value="teacher" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </div>
        <div class="filter-field">
          <label for="admin-status-filter">状态</label>
          <el-select id="admin-status-filter" v-model="statusFilter" class="filter-control">
            <el-option label="全部状态" value="all" />
            <el-option label="已启用" value="active" />
            <el-option label="已停用" value="inactive" />
          </el-select>
        </div>
        <el-button
          v-if="hasFilters"
          class="clear-filter"
          text
          @click="clearFilters"
        >
          清除筛选
        </el-button>
      </div>

      <el-skeleton v-if="loading && !users.length" :rows="6" animated />
      <AppEmptyState
        v-else-if="!filteredUsers.length"
        :icon="User"
        :title="hasFilters ? '没有匹配的用户' : '当前还没有用户账号'"
        :description="hasFilters ? '换个关键词或清空筛选条件再试。' : '有人注册后，账号会出现在这里。'"
      >
        <el-button v-if="hasFilters" type="primary" @click="clearFilters">清除筛选</el-button>
      </AppEmptyState>
      <div v-else class="table-wrap">
        <table class="data-table admin-user-table">
          <caption class="visually-hidden">平台用户目录</caption>
          <thead>
            <tr>
              <th>用户</th>
              <th>角色</th>
              <th>状态</th>
              <th>注册时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in filteredUsers" :key="user.user_id">
              <td>
                <strong class="user-name">{{ user.display_name || '未设置名称' }}</strong>
                <span class="user-email">{{ user.email }}</span>
                <span class="user-id">{{ user.user_id }}</span>
              </td>
              <td>
                <span :class="['status-pill', user.role === 'admin' ? 'active' : 'pending']">
                  {{ roleLabel(user.role) }}
                </span>
              </td>
              <td>
                <span :class="['status-pill', user.is_active ? 'done' : 'failed']">
                  {{ user.is_active ? '已启用' : '已停用' }}
                </span>
              </td>
              <td class="user-date">{{ formatDate(user.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Avatar, CircleCheck, Key, Refresh, User, UserFilled } from '@element-plus/icons-vue'
import { fetchAdminUsers } from '@/api'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'

const users = ref([])
const loading = ref(false)
const pageError = ref('')
const searchQuery = ref('')
const roleFilter = ref('all')
const statusFilter = ref('all')
const lastLoadedAt = ref('')

const teacherCount = computed(() => users.value.filter(user => user.role === 'teacher').length)
const adminCount = computed(() => users.value.filter(user => user.role === 'admin').length)
const activeCount = computed(() => users.value.filter(user => user.is_active).length)
const hasFilters = computed(() => Boolean(
  searchQuery.value.trim() || roleFilter.value !== 'all' || statusFilter.value !== 'all',
))
const filteredUsers = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return users.value.filter((user) => {
    const matchesQuery = !query || [user.email, user.display_name, user.user_id]
      .some(value => String(value || '').toLowerCase().includes(query))
    const matchesRole = roleFilter.value === 'all' || user.role === roleFilter.value
    const matchesStatus = statusFilter.value === 'all'
      || (statusFilter.value === 'active' ? user.is_active : !user.is_active)
    return matchesQuery && matchesRole && matchesStatus
  })
})

async function loadUsers() {
  loading.value = true
  pageError.value = ''
  try {
    const response = await fetchAdminUsers()
    users.value = Array.isArray(response.data) ? response.data : []
    lastLoadedAt.value = new Date().toISOString()
  } catch (error) {
    pageError.value = apiErrorMessage(error, '用户目录加载失败，请检查管理员权限后重试')
  } finally {
    loading.value = false
  }
}

function clearFilters() {
  searchQuery.value = ''
  roleFilter.value = 'all'
  statusFilter.value = 'all'
}

function roleLabel(role) {
  return role === 'admin' ? '管理员' : '教师'
}

function formatDate(value) {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '未记录'
    : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

function apiErrorMessage(error, fallback) {
  return error.response?.data?.error?.message || error.message || fallback
}

onMounted(loadUsers)
</script>

<style scoped>
.admin-heading {
  min-width: 0;
}

.admin-kicker {
  margin: 0 0 var(--space-2);
  color: var(--text-brand);
  font-size: var(--text-xs);
  font-weight: var(--weight-bold);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.admin-stat-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.admin-icon-green {
  background: var(--success-50);
  color: var(--success-600);
}

.admin-icon-amber {
  background: var(--warning-50);
  color: var(--warning-600);
}

.admin-icon-slate {
  background: var(--neutral-100);
  color: var(--text-secondary);
}

.directory-header {
  align-items: flex-end;
}

.directory-meta {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.directory-toolbar {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 160px 160px auto;
  gap: var(--space-3);
  align-items: end;
  margin-bottom: var(--space-4);
}

.filter-field {
  display: grid;
  gap: var(--space-2);
}

.filter-field label {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 700;
}

.filter-control {
  width: 100%;
}

.clear-filter {
  min-height: 32px;
  justify-self: start;
  padding: 0 var(--space-1);
}

.admin-user-table {
  min-width: 720px;
}

.user-name,
.user-email,
.user-id {
  display: block;
}

.user-name {
  color: var(--text-primary);
}

.user-email {
  margin-top: var(--space-1);
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}

.user-id {
  margin-top: var(--space-1);
  color: var(--text-disabled);
  font-size: var(--text-xs);
}

.user-date {
  color: var(--text-secondary);
  white-space: nowrap;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: calc(var(--space-1) * -1);
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 1180px) {
  .admin-stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .directory-toolbar {
    grid-template-columns: minmax(220px, 1fr) 160px 160px;
  }

  .clear-filter {
    grid-column: 1 / -1;
  }
}

@media (max-width: 720px) {
  .admin-stat-grid,
  .directory-toolbar {
    grid-template-columns: 1fr;
  }

  .directory-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .clear-filter {
    grid-column: auto;
  }

  .directory-meta {
    white-space: normal;
  }
}
</style>
