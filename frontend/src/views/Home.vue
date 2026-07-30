<template>
  <div class="page-stack">
    <div class="page-header">
      <div>
        <h1>开始新课</h1>
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

      <!-- 最近会话 -->
      <div v-if="recentSessions.length" class="recent-section">
        <h3>最近会话</h3>
        <div class="recent-list">
          <div
            v-for="s in recentSessions"
            :key="s.id"
            class="recent-item"
            @click="$router.push(`/chat/${s.id}`)"
          >
            <el-icon><ChatDotRound /></el-icon>
            <span class="recent-name">{{ s.name }}</span>
            <span class="recent-time">{{ formatDate(s.created_at) }}</span>
            <el-icon class="recent-arrow"><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { EditPen, Reading, ChatDotRound, ArrowRight } from '@element-plus/icons-vue'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const sessionStore = useSessionStore()

const courseName = ref('')
const loading = ref(false)
const errorMsg = ref('')

const recentSessions = ref([]) // TODO: 从 localStorage 或后端获取

async function handleCreate() {
  const name = courseName.value.trim()
  if (!name || loading.value) return

  loading.value = true
  errorMsg.value = ''

  try {
    const res = await sessionStore.createSession(name)
    // 保存到本地最近列表
    saveRecentSession({ id: res.session_id, name, created_at: new Date().toISOString() })
    router.push(`/chat/${res.session_id}`)
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || err.message || '创建会话失败，请重试'
  } finally {
    loading.value = false
  }
}

function saveRecentSession(session) {
  try {
    const stored = JSON.parse(localStorage.getItem('recent_sessions') || '[]')
    const filtered = stored.filter(s => s.id !== session.id)
    filtered.unshift(session)
    localStorage.setItem('recent_sessions', JSON.stringify(filtered.slice(0, 10)))
    recentSessions.value = filtered.slice(0, 10)
  } catch { /* ignore */ }
}

function loadRecentSessions() {
  try {
    recentSessions.value = JSON.parse(localStorage.getItem('recent_sessions') || '[]')
  } catch { /* ignore */ }
}

function formatDate(ts) {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

loadRecentSessions()
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

.recent-section {
  max-width: 600px;
  margin: 0 auto;
  width: 100%;
}

.recent-section h3 {
  margin: 0 0 12px;
  font-size: 16px;
  color: #374151;
}

.recent-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.recent-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid #e4e9f2;
  border-radius: 8px;
  background: #ffffff;
  cursor: pointer;
  transition: box-shadow 0.15s ease;
}

.recent-item:hover {
  box-shadow: 0 2px 12px rgba(20, 99, 255, 0.1);
  border-color: #c0d4f7;
}

.recent-name {
  flex: 1;
  font-weight: 600;
  color: #111827;
}

.recent-time {
  color: #9ca3af;
  font-size: 13px;
}

.recent-arrow {
  color: #9ca3af;
}
</style>
