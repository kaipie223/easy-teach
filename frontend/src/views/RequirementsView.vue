<template>
  <div class="page-stack entry-page">
    <AppPageHeader
      v-if="!loading && !errorMessage"
      title="需求共创"
      subtitle="正在进入这个教案的 AI 共创会话。"
    />

    <section v-if="loading" class="section-card entry-loading" aria-live="polite">
      <el-skeleton :rows="4" animated />
      <p class="entry-hint">正在打开会话……</p>
    </section>

    <el-alert
      v-else-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
    >
      <template #default>
        <div class="entry-actions">
          <el-button size="small" type="primary" @click="openActiveProject">重试</el-button>
          <el-button size="small" @click="router.push('/')">返回工作台</el-button>
        </div>
      </template>
    </el-alert>

    <AppEmptyState
      v-else
      :icon="Reading"
      title="还没有选中的教案"
      description="需求共创需要一个正在进行的教案，先在工作台里挑一个或新建一个。"
    >
      <el-button type="primary" @click="router.push('/')">返回工作台</el-button>
    </AppEmptyState>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Reading } from '@element-plus/icons-vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'

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
.entry-page { max-width: var(--container-reading); }

.entry-loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.entry-hint {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.entry-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
</style>
