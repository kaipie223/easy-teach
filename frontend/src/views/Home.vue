<template>
  <div class="page-stack">
    <div class="page-header">
      <div>
        <h1>新建项目</h1>
        <p>输入课程名称，TeachMate AI 将引导你完成教学设计</p>
      </div>
    </div>

    <div class="home-hero">
      <div class="hero-card">
        <div class="hero-icon">
          <el-icon :size="40" color="#1463ff"><EditPen /></el-icon>
        </div>
        <h2>创建教学课程</h2>
        <p class="hero-desc">
          告诉我你想教什么，我会帮你：
          <br />• 梳理教学需求
          <br />• 上传参考资料
          <br />• 生成课件和教案
        </p>

        <div class="hero-form">
          <el-input
            v-model="courseName"
            size="large"
            placeholder="输入课程名称，例如：Python入门、初中物理力学……"
            :disabled="loading"
            @keydown.enter="handleCreate"
          >
            <template #prefix>
              <el-icon><Reading /></el-icon>
            </template>
          </el-input>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            :disabled="!courseName.trim()"
            @click="handleCreate"
          >
            开始创建
          </el-button>
        </div>

        <div v-if="errorMsg" class="hero-error">
          <el-alert :title="errorMsg" type="error" show-icon :closable="false" />
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { EditPen, Reading } from '@element-plus/icons-vue'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
import { createProject } from '@/api'

const router = useRouter()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const courseName = ref('')
const loading = ref(false)
const errorMsg = ref('')

const createdProject = ref(null)

async function handleCreate() {
  const name = courseName.value.trim()
  if (!name || loading.value) return

  loading.value = true
  errorMsg.value = ''

  try {
    if (!createdProject.value) {
      const response = await createProject({ title: name, scenario: '' })
      createdProject.value = response.data
      projectStore.selectProject(response.data)
    }
    const res = await sessionStore.createSession(name, createdProject.value.project_id)
    projectStore.setActiveSession(res.session_id)
    router.push(`/chat/${res.session_id}`)
  } catch (err) {
    errorMsg.value = err.response?.data?.error?.message || err.message || '创建会话失败，请重试'
  } finally {
    loading.value = false
  }
}

</script>

<style scoped>
.home-hero {
  display: grid;
  gap: 24px;
}

.hero-card {
  max-width: 600px;
  margin: 0 auto;
  width: 100%;
  text-align: center;
  padding: 40px 32px;
  border: 1px solid #e4e9f2;
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
}

.hero-icon {
  margin-bottom: 16px;
}

.hero-card h2 {
  margin: 0 0 8px;
  font-size: 22px;
  color: #0f172a;
}

.hero-desc {
  color: #64748b;
  font-size: 14px;
  line-height: 1.8;
  margin: 0 0 28px;
}

.hero-form {
  display: flex;
  gap: 10px;
}

.hero-form :deep(.el-input) {
  flex: 1;
}

.hero-error {
  margin-top: 16px;
  text-align: left;
}

</style>
