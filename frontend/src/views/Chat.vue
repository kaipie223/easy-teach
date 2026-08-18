<template>
  <div class="page-stack chat-page">
    <!-- Loading -->
    <div v-if="loading" class="chat-status">
      <el-skeleton :rows="4" animated />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="chat-status">
      <el-alert :title="error" type="error" show-icon :closable="false">
        <template #default>
          <el-button size="small" type="primary" @click="loadSession">重试</el-button>
        </template>
      </el-alert>
    </div>

    <!-- 对话正常 -->
    <template v-else>
      <div class="chat-workspace">
        <section class="chat-main">
      <!-- 顶部信息栏 -->
      <div class="chat-topbar">
        <div class="chat-topbar-left">
          <el-button text @click="$router.push('/home')">
            <el-icon><ArrowLeft /></el-icon>
          </el-button>
          <span class="chat-title">课程对话</span>
          <el-tag v-if="sessionId" size="small" type="info">{{ sessionId }}</el-tag>
        </div>
        <div class="chat-topbar-right">
          <el-button size="small" text :disabled="sseActive || !canGenerate" @click="handleGenerate">
            <el-icon style="margin-right: 4px"><MagicStick /></el-icon>
            生成课件
          </el-button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div class="chat-messages" ref="msgListRef">
        <el-empty v-if="messages.length === 0" description="开始对话吧" />

        <template v-for="(msg, idx) in messages" :key="msg.id">
          <!-- 追问卡片 -->
          <QuestionCard
            v-if="msg.type === 'question'"
            :data="msg.data"
            @submit="answer => handleAnswerQuestion(answer)"
            @skip="handleSkipQuestion"
          />
          <!-- 确认面板 -->
          <ConfirmPanel
            v-else-if="msg.type === 'confirm'"
            :data="msg.data"
            @confirm="handleConfirm"
            @modify="handleModify"
          />
          <!-- 普通消息气泡 -->
          <MessageBubble
            v-else
            :role="msg.role"
            :content="msg.content"
            :timestamp="msg.timestamp"
            :typing="idx === messages.length - 1 && msg.role === 'assistant' && sseActive"
          />
        </template>
      </div>

      <!-- 输入栏 -->
      <div class="chat-footer">
        <ChatInput
          ref="inputRef"
          :disabled="sseActive"
          :sending="sending"
          :show-voice="true"
          @send="handleSend"
          @toggle-voice="toggleVoice"
        />
        <VoiceInput
          v-if="voiceVisible"
          :session-id="sessionId"
          :show-label="true"
          @transcribed="handleVoiceTranscribed"
        />
        <p class="chat-hint">Enter 发送 · Shift+Enter 换行</p>
      </div>
        </section>
        <TeachingBriefPanel
          v-if="projectId"
          :brief="sessionStore.brief"
          :saving="briefSaving"
          :confirming="briefConfirming"
          @save="handleBriefSave"
          @confirm="handleConfirm"
        />
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, MagicStick } from '@element-plus/icons-vue'
import { useSessionStore } from '@/stores/session'
import { SSEClient } from '@/utils/sse'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import QuestionCard from '@/components/chat/QuestionCard.vue'
import ConfirmPanel from '@/components/chat/ConfirmPanel.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import VoiceInput from '@/components/chat/VoiceInput.vue'
import TeachingBriefPanel from '@/components/chat/TeachingBriefPanel.vue'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()

const msgListRef = ref(null)
const inputRef = ref(null)

const loading = ref(false)
const sending = ref(false)
const error = ref('')
const sseActive = ref(false)
const voiceVisible = ref(false)
const briefSaving = ref(false)
const briefConfirming = ref(false)

const sessionId = ref(route.params.sessionId)
const messages = sessionStore.messages // 直接引用 store 的响应式数组
const projectId = computed(() => sessionStore.projectId)
const canGenerate = computed(() => !projectId.value || sessionStore.brief?.status === 'confirmed')

// ── 加载会话 ──────────────────────────────────

async function loadSession() {
  loading.value = true
  error.value = ''
  try {
    await sessionStore.fetchSession(sessionId.value)
    if (projectId.value) {
      await sessionStore.fetchBrief()
    }
  } catch (err) {
    error.value = err.response?.data?.error?.message || err.message || '加载会话失败'
  } finally {
    loading.value = false
  }
}

// ── 发送消息 ──────────────────────────────────

async function handleSend(text) {
  if (!text.trim() || sseActive.value || sending.value) return

  sending.value = true

  // 1. 添加用户消息
  sessionStore.addMessage({ role: 'user', content: text })

  // 2. 滚动到底部
  await nextTick()
  scrollToBottom()

  // 3. 连接 SSE
  sseActive.value = true
  sending.value = false

  const client = new SSEClient(`/api/v1/sessions/${sessionId.value}/chat`, {
    onText: (chunk) => {
      sessionStore.appendToLastMessage(chunk)
      scrollToBottom()
    },
    onQuestion: (data) => {
      sessionStore.addStructuredMessage('question', data)
      scrollToBottom()
    },
    onConfirm: (data) => {
      sessionStore.addStructuredMessage('confirm', data)
      scrollToBottom()
    },
    onDone: () => {
      sseActive.value = false
    },
    onError: (err) => {
      console.error('SSE 错误:', err)
      sseActive.value = false
      sessionStore.addMessage({
        role: 'assistant',
        content: '连接中断，请重试。',
      })
    },
  })

  sessionStore.setSSEClient(client)

  try {
    await client.connect({ message: text })
  } catch {
    sseActive.value = false
  }
}

// ── 追问 / 确认交互 ─────────────────────────────

function handleAnswerQuestion(answer) {
  // 用户回答追问 → 作为新消息发送
  const text = answer.freeText || answer.selected || ''
  if (text) {
    handleSend(text)
  }
}

function handleSkipQuestion() {
  handleSend('跳过')
}

async function handleConfirm() {
  if (briefConfirming.value) return
  briefConfirming.value = true
  error.value = ''
  try {
    if (projectId.value && sessionStore.brief?.status !== 'confirmed') {
      await sessionStore.confirmBrief()
    }
    handleGenerate()
  } catch (err) {
    error.value = err.response?.data?.error?.message || '需求确认失败，请补充信息后重试'
  } finally {
    briefConfirming.value = false
  }
}

function handleModify() {
  // 修改 → 发一条"我要修改"消息让 AI 重新整理
  handleSend('我想修改一些信息')
}

// ── 生成课件 ──────────────────────────────────

function handleGenerate() {
  if (!canGenerate.value) return
  router.push(`/blueprint`)
}

// ── 语音 ──────────────────────────────────────

function toggleVoice() {
  voiceVisible.value = !voiceVisible.value
}

function handleVoiceTranscribed(text) {
  voiceVisible.value = false
  handleSend(text)
}

async function handleBriefSave(changes) {
  if (!projectId.value || briefSaving.value) return
  briefSaving.value = true
  error.value = ''
  try {
    await sessionStore.updateBrief(changes)
  } catch (err) {
    error.value = err.response?.data?.error?.message || '保存需求确认单失败，请重试'
  } finally {
    briefSaving.value = false
  }
}

// ── 滚动 ──────────────────────────────────────

function scrollToBottom() {
  nextTick(() => {
    const el = msgListRef.value
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  })
}

// ── 生命周期 ──────────────────────────────────

onBeforeUnmount(() => {
  sessionStore.resetSession()
})

// 监听路由变化（切换会话）
watch(() => route.params.sessionId, (newId) => {
  if (newId) {
    sessionStore.resetSession()
    sessionId.value = newId
    loadSession()
  }
})

// 初始加载
if (sessionId.value) {
  loadSession()
} else {
  error.value = '无效的会话 ID'
}
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-workspace {
  min-height: 0;
  flex: 1;
  display: flex;
  overflow: hidden;
}

.chat-main {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.chat-status {
  padding: 40px 24px;
  max-width: 640px;
  margin: 0 auto;
  width: 100%;
}

.chat-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid #e6eaf0;
  flex: 0 0 auto;
}

.chat-topbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-title {
  font-weight: 700;
  font-size: 16px;
  color: #0f172a;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 4px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chat-footer {
  flex: 0 0 auto;
  padding-top: 12px;
  border-top: 1px solid #e6eaf0;
}

.chat-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
}

@media (max-width: 980px) {
  .chat-workspace {
    display: block;
    overflow: auto;
  }

  .chat-main {
    min-height: 560px;
  }
}
</style>
