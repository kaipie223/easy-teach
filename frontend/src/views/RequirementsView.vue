<template>
  <div class="page-stack requirements-entry">
    <header class="page-header">
      <div>
        <h1>需求共创</h1>
        <p>正在进入当前项目的 AI 共创会话。</p>
      </div>
    </header>

    <section class="section-card entry-state">
      <el-skeleton v-if="loading" :rows="4" animated />
      <el-alert
        v-else-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
      >
        <template #default>
          <div class="state-actions">
            <el-button size="small" type="primary" @click="openActiveProject">重试</el-button>
            <el-button size="small" @click="router.push('/')">返回工作台</el-button>
          </div>
        </template>
      </el-alert>
      <el-empty v-else description="请先从工作台选择一个项目">
        <el-button type="primary" @click="router.push('/')">返回工作台</el-button>
      </el-empty>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const loading = ref(false)
const errorMessage = ref('')

async function openActiveProject() {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const project = await projectStore.ensureActiveProject()
    if (!project) return
    let sessionId = project.session_id
    if (!sessionId) {
      const session = await sessionStore.createSession(project.title, project.project_id)
      sessionId = session.session_id
      projectStore.setActiveSession(sessionId)
    }
    await router.replace(`/chat/${sessionId}`)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '项目会话加载失败，请重试'
  } finally {
    loading.value = false
  }
}

onMounted(openActiveProject)
</script>

<style scoped>
.requirements-entry {
  max-width: 760px;
  margin: 0 auto;
}

.entry-state {
  min-height: 260px;
  display: grid;
  align-items: center;
}

.state-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
