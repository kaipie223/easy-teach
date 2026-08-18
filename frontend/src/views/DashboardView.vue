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
                <strong>{{ project.title }}</strong>
                <span class="project-id">{{ project.project_id }}</span>
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
import { CircleCheck, Delete, FolderOpened, Plus } from '@element-plus/icons-vue'
import { deleteProject, fetchProjects, restoreProject } from '@/api'

const router = useRouter()
const projects = ref([])
const loading = ref(false)
const errorMessage = ref('')

const activeCount = computed(() => projects.value.filter(project => project.status !== 'deleted').length)
const deletedCount = computed(() => projects.value.filter(project => project.status === 'deleted').length)

async function loadProjects() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetchProjects(true)
    projects.value = response.data
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '项目加载失败，请重试'
  } finally {
    loading.value = false
  }
}

function openProject(project) {
  router.push(project.session_id ? `/chat/${project.session_id}` : `/home?projectId=${project.project_id}`)
}

async function archiveProject(project) {
  try {
    await ElMessageBox.confirm(`归档“${project.title}”？项目数据仍可恢复。`, '归档项目', {
      confirmButtonText: '归档',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteProject(project.project_id)
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
.project-id {
  display: block;
  margin-top: 4px;
  color: #94a3b8;
  font-size: 12px;
}

.link-actions {
  white-space: nowrap;
}

@media (max-width: 720px) {
  .link-actions {
    white-space: normal;
  }
}
</style>
