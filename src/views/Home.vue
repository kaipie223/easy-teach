<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createSession } from '@/api/session'

const router = useRouter()
const courseName = ref('')
const isCreating = ref(false)

const handleCreateSession = async () => {
  const name = courseName.value.trim()
  if (!name) return

  isCreating.value = true
  try {
    const result = await createSession({ courseName: name })
    router.push({
      name: 'Chat',
      params: { sessionId: result.sessionId },
      query: { courseName: name }
    })
  } catch {
    // 错误提示已在 request 拦截器中统一处理
  } finally {
    isCreating.value = false
  }
}
</script>

<template>
  <div class="home-container">
    <div class="home-card">
      <h1 class="home-title">AI 智能课件生成系统</h1>
      <p class="home-subtitle">输入课程名称，开启智能课件创作之旅</p>
      <div class="home-input-group">
        <el-input
          v-model="courseName"
          placeholder="请输入课程名称，例如「人工智能导论」"
          size="large"
          clearable
          @keyup.enter="handleCreateSession"
        />
        <el-button
          type="primary"
          size="large"
          :disabled="!courseName.trim()"
          :loading="isCreating"
          @click="handleCreateSession"
        >
          开始创建会话
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.home-card {
  background: #ffffff;
  border-radius: 16px;
  padding: 60px 80px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
  text-align: center;
  max-width: 560px;
  width: 90%;
}

.home-title {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
  margin: 0 0 12px 0;
  letter-spacing: 2px;
}

.home-subtitle {
  font-size: 14px;
  color: #909399;
  margin: 0 0 36px 0;
}

.home-input-group {
  display: flex;
  gap: 12px;
  align-items: center;
}

.home-input-group .el-input {
  flex: 1;
}
</style>
