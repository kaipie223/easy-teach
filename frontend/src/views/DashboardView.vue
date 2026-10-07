<template>
  <div class="page-stack">
    <AppPageHeader
      title="工作台"
      subtitle="这里是你的门厅：挑一个教案继续，或者开始下一节。"
    >
      <template #actions>
        <el-button type="primary" @click="go('/home')">
          <el-icon><Plus /></el-icon>
          新建教案
        </el-button>
      </template>
    </AppPageHeader>

    <section class="stat-grid motion-stagger" aria-label="教案概览">
      <article class="metric-card">
        <span class="metric-icon"><el-icon><FolderOpened /></el-icon></span>
        <div>
          <span>全部教案</span>
          <strong class="text-tabular">{{ totalDisplay }}</strong>
        </div>
      </article>
      <article class="metric-card">
        <span class="metric-icon"><el-icon><CircleCheck /></el-icon></span>
        <div>
          <span>进行中</span>
          <strong class="text-tabular">{{ activeDisplay }}</strong>
        </div>
      </article>
      <article class="metric-card">
        <span class="metric-icon"><el-icon><Box /></el-icon></span>
        <div>
          <span>已归档</span>
          <strong class="text-tabular">{{ archivedDisplay }}</strong>
        </div>
      </article>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>我的教案</h2>
          <p>教案、对话与生成成果都绑定到当前账号。</p>
        </div>
        <el-button text :loading="loading" @click="loadProjects">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
        class="list-alert"
      >
        <template #default>
          <el-button size="small" type="primary" @click="loadProjects">重试</el-button>
        </template>
      </el-alert>

      <el-skeleton v-if="loading && !projects.length" :rows="4" animated />

      <AppEmptyState
        v-else-if="!projects.length"
        :icon="Reading"
        title="还没有教案"
        description="从一句课程名称开始，AI 会陪你梳理需求、生成可直接上课的课件与教案。"
      >
        <el-button type="primary" @click="go('/home')">
          <el-icon><Plus /></el-icon>
          新建教案
        </el-button>
      </AppEmptyState>

      <ul v-else class="project-list motion-stagger">
        <li
          v-for="project in projects"
          :key="project.project_id"
          class="project-row"
          :class="{ archived: project.status === 'deleted' }"
        >
          <span class="row-mark" aria-hidden="true">{{ initialOf(project.title) }}</span>

          <div class="row-main">
            <div class="row-title">
              <template v-if="editingProjectId === project.project_id">
                <el-input
                  v-model="editingTitle"
                  maxlength="120"
                  size="small"
                  class="rename-input"
                  @keyup.enter="saveProjectTitle(project)"
                  @keyup.esc="cancelProjectTitle"
                />
                <el-button
                  circle
                  text
                  type="primary"
                  aria-label="保存名称"
                  :loading="savingProject"
                  @click="saveProjectTitle(project)"
                >
                  <el-icon><Check /></el-icon>
                </el-button>
                <el-button
                  circle
                  text
                  aria-label="取消重命名"
                  :disabled="savingProject"
                  @click="cancelProjectTitle"
                >
                  <el-icon><Close /></el-icon>
                </el-button>
              </template>
              <template v-else>
                <strong>{{ project.title }}</strong>
                <el-tag
                  v-if="projectStore.activeProjectId === project.project_id"
                  size="small"
                  type="primary"
                >
                  当前
                </el-tag>
              </template>
            </div>
            <p class="row-meta">
              <span>{{ project.scenario || '未设置场景' }}</span>
              <span class="dot" aria-hidden="true">·</span>
              <span>最近更新 {{ formatDate(project.updated_at || project.created_at) }}</span>
            </p>
          </div>

          <span
            class="status-pill"
            :class="project.status === 'deleted' ? 'draft' : 'running'"
          >
            {{ project.status === 'deleted' ? '已归档' : '进行中' }}
          </span>

          <div class="row-actions">
            <el-button
              v-if="project.status !== 'deleted'"
              type="primary"
              size="small"
              @click="openProject(project)"
            >
              {{ project.session_id ? '继续' : '开始' }}
            </el-button>

            <el-dropdown trigger="click" @command="command => handleCommand(command, project)">
              <el-button size="small" aria-label="更多操作">
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-if="project.status !== 'deleted'"
                    command="rename"
                  >
                    重命名
                  </el-dropdown-item>
                  <el-dropdown-item
                    v-if="project.status !== 'deleted'"
                    divided
                    command="archive"
                  >
                    <span class="danger-item">归档教案</span>
                  </el-dropdown-item>
                  <el-dropdown-item v-else command="restore">恢复教案</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Box,
  Check,
  CircleCheck,
  Close,
  FolderOpened,
  MoreFilled,
  Plus,
  Reading,
  Refresh,
} from '@element-plus/icons-vue'
import { deleteProject, restoreProject, updateProject } from '@/api'
import { useProjectStore } from '@/stores/project'
import { useCountUp } from '@/composables/useCountUp'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'

const router = useRouter()
const projectStore = useProjectStore()
const projects = ref([])
const loading = ref(false)
const errorMessage = ref('')
const editingProjectId = ref('')
const editingTitle = ref('')
const savingProject = ref(false)

const activeCount = computed(
  () => projects.value.filter(project => project.status !== 'deleted').length,
)
const deletedCount = computed(
  () => projects.value.filter(project => project.status === 'deleted').length,
)

// 统计数字"涨"上去；reduced-motion 下直接显示终值
const totalDisplay = useCountUp(computed(() => projects.value.length))
const activeDisplay = useCountUp(activeCount)
const archivedDisplay = useCountUp(deletedCount)

function initialOf(title) {
  return String(title || '课').trim().slice(0, 1)
}

async function loadProjects() {
  loading.value = true
  errorMessage.value = ''
  try {
    projects.value = await projectStore.loadProjects(true)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '项目加载失败，请重试'
  } finally {
    loading.value = false
  }
}

function openProject(project) {
  projectStore.selectProject(project)
  router.push('/requirements')
}

function editProjectTitle(project) {
  editingProjectId.value = project.project_id
  editingTitle.value = project.title
}

function cancelProjectTitle() {
  editingProjectId.value = ''
  editingTitle.value = ''
}

async function saveProjectTitle(project) {
  const title = editingTitle.value.trim()
  if (!title) {
    ElMessage.warning('教案名称不能为空')
    return
  }
  savingProject.value = true
  try {
    const response = await updateProject(project.project_id, { title })
    Object.assign(project, response.data)
    if (projectStore.activeProjectId === project.project_id) {
      projectStore.updateActiveProject(response.data)
    }
    cancelProjectTitle()
    ElMessage.success('教案名称已更新')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.message || '名称更新失败')
  } finally {
    savingProject.value = false
  }
}

async function archiveProject(project) {
  try {
    await ElMessageBox.confirm(
      `归档「${project.title}」？归档后仍可恢复，不会删除任何数据。`,
      '归档教案',
      { confirmButtonText: '归档', cancelButtonText: '取消', type: 'warning' },
    )
    await deleteProject(project.project_id)
    if (projectStore.activeProjectId === project.project_id) {
      projectStore.clearActiveProject()
    }
    await loadProjects()
    ElMessage.success('教案已归档')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error.response?.data?.error?.message || '归档失败，请重试')
    }
  }
}

async function restore(project) {
  try {
    await restoreProject(project.project_id)
    await loadProjects()
    ElMessage.success('教案已恢复')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.message || '恢复失败，请重试')
  }
}

function handleCommand(command, project) {
  if (command === 'rename') editProjectTitle(project)
  if (command === 'archive') archiveProject(project)
  if (command === 'restore') restore(project)
}

function go(path) {
  if (path === '/home') projectStore.clearActiveProject()
  router.push(path)
}

function formatDate(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '未记录'
    : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

onMounted(loadProjects)
</script>

<style scoped>
.list-alert {
  margin-bottom: var(--space-4);
}

/* ── 教案列表：列表行取代表格，避免窄屏横向滚动 ─────────── */
.project-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.project-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-2);
  border-bottom: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  transition: background-color var(--duration-fast) var(--ease-standard);
}

.project-row:last-child { border-bottom: 0; }

.project-row:hover { background: var(--neutral-50); }

.project-row.archived { opacity: 0.66; }

.row-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-lg);
  background: var(--brand-50);
  color: var(--text-brand);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.project-row.archived .row-mark {
  background: var(--neutral-100);
  color: var(--text-tertiary);
}

.row-main { min-width: 0; }

.row-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.row-title strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rename-input { max-width: 280px; }

.row-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-1);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.dot { color: var(--neutral-400); }

.row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  justify-content: flex-end;
}

.danger-item { color: var(--danger-600); }

@media (max-width: 768px) {
  /* 窄屏：标题独占一行，状态与操作并排在下一行，避免错位与拥挤 */
  .project-row {
    grid-template-columns: 32px minmax(0, 1fr) auto;
    grid-template-areas:
      'mark main main'
      'mark status actions';
    align-items: center;
    gap: var(--space-2) var(--space-3);
    padding: var(--space-4) 0;
  }

  .row-mark {
    grid-area: mark;
    width: 32px;
    height: 32px;
    align-self: start;
    border-radius: var(--radius-md);
  }

  .row-main { grid-area: main; }
  .status-pill { grid-area: status; justify-self: start; }

  .row-actions {
    grid-area: actions;
    justify-self: end;
  }

  /* 触控目标不小于 36px；图标按钮再单独补足 */
  .row-actions :deep(.el-button) {
    min-width: 36px;
    height: 36px;
  }

  .row-meta { flex-wrap: wrap; }
}
</style>
