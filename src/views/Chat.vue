<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import type { Message } from '@/types/chat'
import { useChatStore } from '@/store/chatStore'
import { getSession } from '@/api/session'

const router = useRouter()
const route = useRoute()
const chatStore = useChatStore()

// ---------- 页面状态 ----------
const pageLoading = ref(true)
const pageError = ref('')

// 课程名称（优先从 API 获取，回退到 query）
const courseName = ref((route.query.courseName as string) || '未指定课程')

// ---------- Mock 兜底数据（API 返回空时使用，保证页面可交互） ----------
const MOCK_MESSAGES: Message[] = [
  {
    id: 'mock_1',
    role: 'assistant',
    eventType: 'text',
    content: '您好！欢迎使用 AI 智能课件生成系统。我已收到您的课程，接下来我将引导您逐步完成课件大纲的设计与生成。',
    timestamp: Date.now() - 120000
  },
  {
    id: 'mock_2',
    role: 'assistant',
    eventType: 'question',
    content: '为了更精准地为您生成课件，请选择以下信息：',
    questionData: {
      title: '请选择课程类型与目标受众',
      options: [
        { label: '🎓 高等教育 - 本科课程', value: 'undergraduate' },
        { label: '📚 高等教育 - 研究生课程', value: 'graduate' },
        { label: '💼 职业培训 - 企业内训', value: 'corporate' },
        { label: '🏫 K12 教育 - 中学课程', value: 'k12' }
      ]
    },
    timestamp: Date.now() - 60000
  },
  {
    id: 'mock_3',
    role: 'assistant',
    eventType: 'confirm',
    content: '根据您的课程主题与受众选择，系统已为您生成以下课件大纲，请确认：',
    confirmData: {
      courseName: courseName.value,
      targetAudience: '本科学生',
      chapters: [
        { title: '第一章：课程概述与背景', detail: '介绍课程的整体框架、学习目标及学科发展背景' },
        { title: '第二章：核心概念与理论基础', detail: '深入讲解关键概念、基本原理与经典理论模型' },
        { title: '第三章：方法论与实践应用', detail: '通过案例分析与实操演练，掌握核心方法' },
        { title: '第四章：前沿发展与趋势展望', detail: '探讨学科前沿动态与未来发展方向' },
        { title: '第五章：总结回顾与课后任务', detail: '知识点梳理、课后练习与拓展阅读推荐' }
      ]
    },
    timestamp: Date.now()
  }
]

const messages = ref<Message[]>([])

// ---------- 加载会话历史 ----------
onMounted(async () => {
  const sessionId = route.params.sessionId as string
  pageLoading.value = true
  pageError.value = ''

  try {
    const session = await getSession(sessionId)
    courseName.value = session.courseName || courseName.value

    if (session.messages && session.messages.length > 0) {
      messages.value = session.messages
    } else {
      // 后端无历史消息时使用 Mock 兜底，保证页面可演示
      messages.value = MOCK_MESSAGES
    }
  } catch {
    pageError.value = '加载会话失败，请检查网络连接或返回首页重试'
  } finally {
    pageLoading.value = false
  }
})

// ---------- 输入与发送 ----------
const inputText = ref('')
const chatListRef = ref<HTMLElement>()

// 自动滚动到底部
const scrollToBottom = async () => {
  await nextTick()
  if (chatListRef.value) {
    chatListRef.value.scrollTop = chatListRef.value.scrollHeight
  }
}

watch(messages, scrollToBottom, { deep: true })

const handleSend = () => {
  const text = inputText.value.trim()
  if (!text) return

  const userMsg: Message = {
    id: `msg_${Date.now()}`,
    role: 'user',
    eventType: 'text',
    content: text,
    timestamp: Date.now()
  }
  messages.value.push(userMsg)
  inputText.value = ''
}

// ---------- 追问选项点击 ----------
const handleOptionClick = (optionValue: string) => {
  const userMsg: Message = {
    id: `msg_${Date.now()}`,
    role: 'user',
    eventType: 'text',
    content: `我选择：${optionValue}`,
    timestamp: Date.now()
  }
  messages.value.push(userMsg)
  // 模拟：此处可对接后端，根据选择触发下一轮交互
}

// ---------- 重试加载 ----------
const handleRetryLoad = async () => {
  const sessionId = route.params.sessionId as string
  pageError.value = ''
  pageLoading.value = true
  try {
    const session = await getSession(sessionId)
    courseName.value = session.courseName || courseName.value
    messages.value = session.messages?.length ? session.messages : MOCK_MESSAGES
  } catch {
    pageError.value = '加载会话失败，请检查网络连接或返回首页重试'
  } finally {
    pageLoading.value = false
  }
}

// ---------- 确认生成 ----------
const handleConfirmGenerate = () => {
  // 将当前大纲写入 Pinia store，供 Preview 页面读取
  const confirmMsg = messages.value.find((m) => m.eventType === 'confirm')
  if (confirmMsg?.confirmData) {
    chatStore.setCourseName(confirmMsg.confirmData.courseName)
    chatStore.updateOutline(confirmMsg.confirmData.chapters)
  }
  const taskId = `task_${Date.now()}`
  router.push({ name: 'Preview', params: { taskId } })
}
</script>

<template>
  <div class="chat-layout">
    <!-- 左侧：参考资料上传占位区 -->
    <aside class="chat-sidebar">
      <div class="sidebar-header">
        <h3>📁 参考资料</h3>
      </div>
      <div class="sidebar-body">
        <div class="upload-placeholder">
          <el-icon :size="48"><i class="el-icon-upload" /></el-icon>
          <p>拖拽文件到此处上传</p>
          <p class="hint">支持 PDF、PPT、Word、图片等格式</p>
          <el-divider />
          <p class="hint">或点击下方按钮选择文件</p>
          <el-button type="primary" plain size="small" disabled>
            上传资料
          </el-button>
        </div>
      </div>
    </aside>

    <!-- 右侧：主对话区域 -->
    <main class="chat-main">
      <!-- 顶部标题栏 -->
      <header class="chat-header">
        <span class="chat-title">{{ courseName }}</span>
        <el-tag type="info" size="small">会话 ID: {{ route.params.sessionId }}</el-tag>
      </header>

      <!-- 消息列表 -->
      <div
        ref="chatListRef"
        class="chat-list"
        v-loading="pageLoading"
      >
        <!-- 加载失败 -->
        <el-alert
          v-if="pageError && !pageLoading"
          type="error"
          title="加载失败"
          :description="pageError"
          show-icon
          :closable="false"
          class="chat-error-alert"
        >
          <template #default>
            <el-button type="primary" size="small" style="margin-top: 12px" @click="handleRetryLoad">
              重新加载
            </el-button>
          </template>
        </el-alert>

        <!-- 无消息数据 -->
        <el-empty
          v-if="!pageLoading && !pageError && messages.length === 0"
          description="暂无消息，开始对话吧"
          :image-size="120"
        />

        <!-- 消息列表 -->
        <template v-if="!pageLoading && !pageError && messages.length > 0">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message-wrapper"
          :class="{ 'message-user': msg.role === 'user', 'message-assistant': msg.role === 'assistant' }"
        >
          <!-- 角色头像 -->
          <div class="message-avatar">
            <el-avatar
              :size="36"
              :style="{ backgroundColor: msg.role === 'user' ? '#409EFF' : '#67C23A' }"
            >
              {{ msg.role === 'user' ? '我' : 'AI' }}
            </el-avatar>
          </div>

          <!-- 消息气泡 -->
          <div class="message-bubble">
            <!-- eventType === 'text': 纯文本 -->
            <div v-if="msg.eventType === 'text'" class="bubble-text">
              {{ msg.content }}
            </div>

            <!-- eventType === 'question': 追问卡片 -->
            <div v-else-if="msg.eventType === 'question'" class="bubble-question">
              <p class="bubble-text">{{ msg.content }}</p>
              <div class="question-card">
                <h4 class="question-title">{{ msg.questionData?.title }}</h4>
                <div class="question-options">
                  <el-button
                    v-for="opt in msg.questionData?.options"
                    :key="opt.value"
                    type="default"
                    size="default"
                    class="option-btn"
                    @click="handleOptionClick(opt.value)"
                  >
                    {{ opt.label }}
                  </el-button>
                </div>
              </div>
            </div>

            <!-- eventType === 'confirm': 大纲确认面板 -->
            <div v-else-if="msg.eventType === 'confirm'" class="bubble-confirm">
              <p class="bubble-text">{{ msg.content }}</p>
              <div class="confirm-panel">
                <div class="confirm-meta">
                  <span><strong>课程名称：</strong>{{ msg.confirmData?.courseName }}</span>
                  <span><strong>目标受众：</strong>{{ msg.confirmData?.targetAudience }}</span>
                </div>
                <el-divider />
                <ul class="chapter-list">
                  <li
                    v-for="(ch, idx) in msg.confirmData?.chapters"
                    :key="idx"
                    class="chapter-item"
                  >
                    <span class="chapter-index">{{ idx + 1 }}</span>
                    <div class="chapter-content">
                      <strong>{{ ch.title }}</strong>
                      <p>{{ ch.detail }}</p>
                    </div>
                  </li>
                </ul>
                <el-divider />
                <div class="confirm-actions">
                  <el-button size="default" @click="() => {}">重新生成</el-button>
                  <el-button type="primary" size="default" @click="handleConfirmGenerate">
                    确认生成
                  </el-button>
                </div>
              </div>
            </div>

            <!-- eventType === 'done': 完成提示 -->
            <div v-else-if="msg.eventType === 'done'" class="bubble-text">
              ✅ {{ msg.content }}
            </div>
          </div>
        </div>
        </template>
      </div>

      <!-- 底部输入区 -->
      <footer class="chat-input-area">
        <el-input
          v-model="inputText"
          placeholder="输入消息，按 Enter 发送..."
          size="large"
          clearable
          @keyup.enter="handleSend"
        >
          <template #append>
            <el-button
              type="primary"
              :disabled="!inputText.trim()"
              @click="handleSend"
            >
              发送
            </el-button>
          </template>
        </el-input>
      </footer>
    </main>
  </div>
</template>

<style scoped>
/* ========== 整体布局 ========== */
.chat-layout {
  display: flex;
  height: 100vh;
  background: #f5f7fa;
}

/* ========== 左侧边栏 ========== */
.chat-sidebar {
  width: 320px;
  min-width: 320px;
  background: #ffffff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 20px 24px 16px;
  border-bottom: 1px solid #ebeef5;
}

.sidebar-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.sidebar-body {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.upload-placeholder {
  text-align: center;
  color: #909399;
  width: 100%;
  padding: 40px 20px;
  border: 2px dashed #dcdfe6;
  border-radius: 12px;
  transition: border-color 0.3s;
}

.upload-placeholder:hover {
  border-color: #409eff;
}

.upload-placeholder p {
  margin: 8px 0;
  font-size: 14px;
}

.upload-placeholder .hint {
  font-size: 12px;
  color: #c0c4cc;
}

/* ========== 主对话区域 ========== */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-header {
  padding: 16px 24px;
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.chat-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

/* ========== 消息列表 ========== */
.chat-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.chat-list::-webkit-scrollbar {
  width: 6px;
}

.chat-list::-webkit-scrollbar-thumb {
  background: #dcdfe6;
  border-radius: 3px;
}

/* ========== 错误提示 ========== */
.chat-error-alert {
  margin: 20px 0;
}

/* ========== 消息条目 ========== */
.message-wrapper {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  max-width: 80%;
}

.message-user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-assistant {
  align-self: flex-start;
}

.message-avatar {
  flex-shrink: 0;
}

/* ========== 消息气泡 ========== */
.message-bubble {
  padding: 14px 18px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
  color: #303133;
  word-break: break-word;
}

.message-user .message-bubble {
  background: #409eff;
  color: #ffffff;
  border-bottom-right-radius: 4px;
}

.message-assistant .message-bubble {
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.bubble-text {
  white-space: pre-wrap;
}

/* ========== 追问卡片 ========== */
.question-card {
  margin-top: 14px;
  padding: 18px;
  background: #fafbfc;
  border-radius: 10px;
  border: 1px solid #ebeef5;
}

.question-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 14px 0;
}

.question-options {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.option-btn {
  border-radius: 20px !important;
  transition: all 0.3s;
}

.option-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.2);
}

/* ========== 确认面板 ========== */
.confirm-panel {
  margin-top: 14px;
  padding: 20px;
  background: #fafbfc;
  border-radius: 10px;
  border: 1px solid #ebeef5;
}

.confirm-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 14px;
  color: #606266;
}

.chapter-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chapter-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.chapter-index {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #409eff;
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.chapter-content {
  flex: 1;
}

.chapter-content strong {
  font-size: 14px;
  color: #303133;
}

.chapter-content p {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: #909399;
  line-height: 1.5;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* ========== 底部输入区 ========== */
.chat-input-area {
  padding: 16px 24px;
  background: #ffffff;
  border-top: 1px solid #e4e7ed;
  flex-shrink: 0;
}
</style>
