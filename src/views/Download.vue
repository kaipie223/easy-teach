<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/store/chatStore'

const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()

const fileId = route.params.fileId as string

// ---------- 下载操作 ----------
const handleDownload = (format: string) => {
  ElMessage.success(`${format} 格式课件正在打包下载，请稍候...`)
}

// ---------- 返回首页 ----------
const handleBackHome = () => {
  chatStore.resetStore()
  router.push({ name: 'Home' })
}
</script>

<template>
  <div class="download-container">
    <div class="download-card">
      <!-- 成功结果 -->
      <el-result
        icon="success"
        title="课件已成功生成！"
        sub-title="您的 AI 智能课件已准备就绪，可选择以下格式下载"
      >
        <template #extra>
          <div class="download-actions">
            <el-button
              type="primary"
              size="large"
              :icon="'Document'"
              @click="handleDownload('PPTX')"
            >
              下载 PPTX
            </el-button>
            <el-button
              type="success"
              size="large"
              @click="handleDownload('PDF')"
            >
              下载 PDF
            </el-button>
            <el-button
              type="warning"
              size="large"
              @click="handleDownload('Word 讲义')"
            >
              下载 Word 讲义
            </el-button>
          </div>

          <el-divider />

          <div class="meta-info">
            <el-descriptions :column="1" border size="small" title="课件信息">
              <el-descriptions-item label="课件名称">
                {{ chatStore.courseName || '未命名课件' }}
              </el-descriptions-item>
              <el-descriptions-item label="文件 ID">{{ fileId }}</el-descriptions-item>
              <el-descriptions-item label="章节数">
                {{ chatStore.currentOutline.length }} 章
              </el-descriptions-item>
              <el-descriptions-item label="生成状态">
                <el-tag type="success" size="small">已完成</el-tag>
              </el-descriptions-item>
            </el-descriptions>
          </div>

          <div class="back-area">
            <el-button size="large" @click="handleBackHome">
              ← 返回首页，创建新课件
            </el-button>
          </div>
        </template>
      </el-result>
    </div>
  </div>
</template>

<style scoped>
.download-container {
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: #f5f7fa;
  padding: 40px 20px;
}

.download-card {
  background: #ffffff;
  border-radius: 16px;
  padding: 48px 56px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
  max-width: 640px;
  width: 100%;
}

.download-actions {
  display: flex;
  justify-content: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.meta-info {
  max-width: 480px;
  margin: 0 auto;
}

.back-area {
  margin-top: 24px;
  text-align: center;
}
</style>
