<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/store/chatStore'

const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()

const taskId = route.params.taskId as string
const isGenerating = ref(false)

// ---------- 进度步骤 ----------
const activeStep = ref(1)
const steps = [
  { title: '需求沟通', description: '确认课程信息' },
  { title: '大纲预览', description: '审核课件大纲' },
  { title: '课件生成', description: 'AI 自动生成' }
]

// ---------- 编辑章节 ----------
const handleEditChapter = (chapterTitle: string) => {
  ElMessage.info(`「${chapterTitle}」编辑功能开发中，敬请期待`)
}

// ---------- 确认生成 ----------
const handleConfirmAndGenerate = async () => {
  isGenerating.value = true
  chatStore.setGenerationStatus('generating')
  activeStep.value = 2

  // 模拟 2 秒生成
  await new Promise((resolve) => setTimeout(resolve, 2000))

  chatStore.setGenerationStatus('success')
  activeStep.value = 3
  isGenerating.value = false

  const fileId = `file_${Date.now()}`
  router.push({ name: 'Download', params: { fileId } })
}

// ---------- 返回上一步 ----------
const handleBack = () => {
  router.back()
}
</script>

<template>
  <div class="preview-container">
    <!-- 顶部进度条 -->
    <header class="preview-header">
      <el-button text @click="handleBack">← 返回对话</el-button>
      <h2>课件大纲预览</h2>
      <el-tag type="warning" size="small">任务 ID: {{ taskId }}</el-tag>
    </header>

    <!-- 步骤条 -->
    <div class="steps-wrapper">
      <el-steps :active="activeStep" align-center finish-status="success">
        <el-step
          v-for="(step, idx) in steps"
          :key="idx"
          :title="step.title"
          :description="step.description"
        />
      </el-steps>
    </div>

    <!-- 大纲内容区 -->
    <div class="preview-body">
      <div class="outline-section">
        <div class="section-header">
          <h3>📋 {{ chatStore.courseName || '课件' }} · 章节大纲</h3>
          <el-tag type="info" size="small">共 {{ chatStore.currentOutline.length }} 章</el-tag>
        </div>

        <el-card v-if="chatStore.currentOutline.length === 0" shadow="never" class="empty-card">
          <el-empty description="暂无大纲数据，请返回对话页面重新生成" :image-size="100" />
        </el-card>

        <div v-else class="chapter-list">
          <el-card
            v-for="(ch, idx) in chatStore.currentOutline"
            :key="idx"
            shadow="hover"
            class="chapter-card"
          >
            <div class="chapter-index-badge">{{ idx + 1 }}</div>
            <div class="chapter-body">
              <div class="chapter-title-row">
                <strong class="chapter-title">{{ ch.title }}</strong>
                <el-button
                  text
                  type="primary"
                  size="small"
                  class="edit-btn"
                  @click="handleEditChapter(ch.title)"
                >
                  <el-icon><i class="el-icon-edit" /></el-icon>
                  编辑
                </el-button>
              </div>
              <p class="chapter-detail">{{ ch.detail }}</p>
            </div>
          </el-card>
        </div>

        <!-- 确认操作区 -->
        <div class="confirm-area">
          <el-button size="large" @click="handleBack">返回修改</el-button>
          <el-button
            type="primary"
            size="large"
            :loading="isGenerating"
            @click="handleConfirmAndGenerate"
          >
            {{ isGenerating ? '正在生成课件...' : '确认大纲并生成课件' }}
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ========== 整体布局 ========== */
.preview-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  overflow: hidden;
}

/* ========== 顶部标题栏 ========== */
.preview-header {
  padding: 14px 24px;
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
}

.preview-header h2 {
  flex: 1;
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

/* ========== 步骤条 ========== */
.steps-wrapper {
  padding: 28px 48px 8px;
  background: #ffffff;
  border-bottom: 1px solid #ebeef5;
  flex-shrink: 0;
}

/* ========== 大纲内容区 ========== */
.preview-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.preview-body::-webkit-scrollbar {
  width: 6px;
}

.preview-body::-webkit-scrollbar-thumb {
  background: #dcdfe6;
  border-radius: 3px;
}

.outline-section {
  max-width: 900px;
  margin: 0 auto;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.section-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.empty-card {
  border-radius: 12px;
}

/* ========== 章节卡片 ========== */
.chapter-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.chapter-card {
  border-radius: 12px;
  transition: box-shadow 0.3s, transform 0.2s;
}

.chapter-card:hover {
  transform: translateY(-2px);
}

.chapter-card :deep(.el-card__body) {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  padding: 20px 24px;
}

.chapter-index-badge {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, #409eff, #337ecc);
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 700;
  flex-shrink: 0;
}

.chapter-body {
  flex: 1;
  min-width: 0;
}

.chapter-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chapter-title {
  font-size: 15px;
  color: #303133;
}

.edit-btn {
  flex-shrink: 0;
  opacity: 0.5;
  transition: opacity 0.2s;
}

.chapter-card:hover .edit-btn {
  opacity: 1;
}

.chapter-detail {
  margin: 8px 0 0 0;
  font-size: 13px;
  color: #909399;
  line-height: 1.6;
}

/* ========== 确认操作区 ========== */
.confirm-area {
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-top: 32px;
  padding-bottom: 32px;
}
</style>
