<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>工作台</h1>
        <p>查看你的教学项目，并从上次进度继续。</p>
      </div>
      <el-button type="primary" @click="go('/home')">
        <el-icon><Plus /></el-icon>
        新建项目
      </el-button>
    </header>

    <section class="stat-grid" aria-label="项目概览">
      <article class="metric-card">
        <span class="metric-icon"><el-icon><FolderOpened /></el-icon></span>
        <div><span>全部项目</span><strong>{{ projects.length }}</strong></div>
      </article>
      <article class="metric-card">
        <span class="metric-icon"><el-icon><CircleCheck /></el-icon></span>
        <div><span>进行中</span><strong>{{ activeCount }}</strong></div>
      </article>
      <article class="metric-card">
        <span class="metric-icon"><el-icon><Delete /></el-icon></span>
        <div><span>已归档</span><strong>{{ deletedCount }}</strong></div>
      </article>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>教学项目</h2>
          <p>项目、会话和生成成果会绑定到当前账号。</p>
        </div>
        <el-button text :loading="loading" @click="loadProjects">刷新</el-button>
      </div>

      <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
      <el-skeleton v-if="loading && !projects.length" :rows="4" animated />
      <el-empty v-else-if="!projects.length" description="还没有教学项目" />

      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>项目名称</th>
              <th>场景</th>
              <th>状态</th>
              <th>最近更新</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="project in projects" :key="project.project_id">
              <td>
                <div v-if="editingProjectId === project.project_id" class="title-editor">
                  <el-input v-model="editingTitle" maxlength="120" @keyup.enter="saveProjectTitle(project)" @keyup.esc="cancelProjectTitle" />
                  <el-tooltip content="保存名称"><el-button circle text type="primary" :loading="savingProject" @click="saveProjectTitle(project)"><el-icon><Check /></el-icon></el-button></el-tooltip>
                  <el-tooltip content="取消编辑"><el-button circle text :disabled="savingProject" @click="cancelProjectTitle"><el-icon><Close /></el-icon></el-button></el-tooltip>
                </div>
                <div v-else class="project-title">
                  <strong>{{ project.title }}</strong>
                  <el-tag v-if="projectStore.activeProjectId === project.project_id" size="small" type="primary">当前项目</el-tag>
                  <el-tooltip v-if="project.status !== 'deleted'" content="编辑项目名称"><el-button circle text @click="editProjectTitle(project)"><el-icon><EditPen /></el-icon></el-button></el-tooltip>
                </div>
              </td>
              <td>{{ project.scenario || '未设置' }}</td>
              <td><span :class="['status-pill', project.status === 'deleted' ? 'draft' : 'running']">
                {{ project.status === 'deleted' ? '已归档' : '进行中' }}
              </span></td>
              <td>{{ formatDate(project.updated_at || project.created_at) }}</td>
              <td class="link-actions">
                <el-button v-if="project.status !== 'deleted'" link type="primary" @click="openProject(project)">
                  {{ project.session_id ? '继续' : '开始' }}
                </el-button>
                <el-button v-if="project.status !== 'deleted'" link type="danger" @click="archiveProject(project)">
                  归档
                </el-button>
                <el-button v-else link type="primary" @click="restore(project)">恢复</el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, CircleCheck, Close, Delete, EditPen, FolderOpened, Plus } from '@element-plus/icons-vue'
import { deleteProject, restoreProject, updateProject } from '@/api'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const projectStore = useProjectStore()
const projects = ref([])
const loading = ref(false)
const errorMessage = ref('')
const editingProjectId = ref('')
const editingTitle = ref('')
const savingProject = ref(false)

const activeCount = computed(() => projects.value.filter(project => project.status !== 'deleted').length)
const deletedCount = computed(() => projects.value.filter(project => project.status === 'deleted').length)

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
    ElMessage.warning('项目名称不能为空')
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
    ElMessage.success('项目名称已更新')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.message || '项目名称更新失败')
  } finally {
    savingProject.value = false
  }
}

async function archiveProject(project) {
  try {
    await ElMessageBox.confirm(`归档“${project.title}”？项目数据仍可恢复。`, '归档项目', {
      confirmButtonText: '归档',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteProject(project.project_id)
    if (projectStore.activeProjectId === project.project_id) {
      projectStore.clearActiveProject()
    }
    await loadProjects()
    ElMessage.success('项目已归档')
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
    ElMessage.success('项目已恢复')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.message || '恢复失败，请重试')
  }
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
.project-title, .title-editor { display: flex; align-items: center; gap: 6px; min-width: 220px; }
.title-editor :deep(.el-input) { max-width: 280px; }

.link-actions {
  white-space: nowrap;
  text-align: right;
}

@media (max-width: 720px) {
  .link-actions {
    white-space: normal;
  }
}
</style>
