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
          <el-button text aria-label="返回工作台" @click="$router.push('/')">
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
        <el-alert
          v-if="aiError"
          :title="aiError.message"
          :description="aiError.suggested_action"
          type="error"
          show-icon
          closable
          @close="aiError = null"
        >
          <el-button
            v-if="initialStartFailed"
            size="small"
            type="primary"
            @click="startInitialConversation"
          >
            重试 AI 开场
          </el-button>
        </el-alert>
        <el-empty v-if="messages.length === 0" description="开始对话吧" />

        <template v-for="(msg, idx) in messages" :key="msg.id">
          <!-- 正文气泡先画：流式追问与它下面的卡片是同一条消息 -->
          <MessageBubble
            v-if="msg.content"
            :role="msg.role"
            :content="msg.content"
            :timestamp="msg.timestamp"
            :typing="idx === messages.length - 1 && msg.role === 'assistant' && sseActive"
          />
          <!-- 追问卡片 -->
          <QuestionCard
            v-if="msg.type === 'question'"
            :data="msg.data"
            :prompt="msg.content ? '' : null"
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
        </template>
      </div>

      <!-- 输入栏 -->
      <div class="chat-footer">
        <ChatInput
          ref="inputRef"
          :disabled="sseActive"
          :sending="sending"
          :recording="voiceRecording"
          :show-voice="true"
          @send="handleSend"
          @toggle-voice="toggleVoice"
        />
        <VoiceInput
          v-if="voiceVisible"
          :session-id="sessionId"
          :show-label="true"
          :auto-start="true"
          @recording="voiceRecording = $event"
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
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, MagicStick } from '@element-plus/icons-vue'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
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
const projectStore = useProjectStore()

const msgListRef = ref(null)
const inputRef = ref(null)

const loading = ref(false)
const sending = ref(false)
const error = ref('')
const sseActive = ref(false)
const voiceVisible = ref(false)
const voiceRecording = ref(false)
const briefSaving = ref(false)
const briefConfirming = ref(false)
const aiError = ref(null)
const initialStartFailed = ref(false)

const sessionId = ref(route.params.sessionId)
const { messages, projectId, brief } = storeToRefs(sessionStore)
const canGenerate = computed(() => !projectId.value || brief.value?.status === 'confirmed')

// ── 加载会话 ──────────────────────────────────

async function loadSession() {
  loading.value = true
  error.value = ''
  let shouldStartConversation = false
  try {
    const loadedSession = await sessionStore.fetchSession(sessionId.value)
    if (projectId.value) {
      await projectStore.selectProjectById(projectId.value)
      projectStore.setActiveSession(sessionId.value)
    }
    if (projectId.value && loadedSession.brief_id) {
      await sessionStore.fetchBrief()
    }
    shouldStartConversation = loadedSession.messages.length === 0
      || loadedSession.messages.every(message => message.msg_type === 'error')
  } catch (err) {
    error.value = err.response?.data?.error?.message || err.message || '加载会话失败'
  } finally {
    loading.value = false
  }
  if (shouldStartConversation) await startInitialConversation()
}

// ── 发送消息 ──────────────────────────────────

async function handleSend(text) {
  if (!text.trim() || sseActive.value || sending.value) return

  sending.value = true
  aiError.value = null

  // 1. 添加用户消息
  sessionStore.addMessage({ role: 'user', content: text })

  // 2. 滚动到底部
  await nextTick()
  scrollToBottom()

  sending.value = false
  await connectAIStream(`/api/v1/sessions/${sessionId.value}/chat`, { message: text })
}

async function startInitialConversation() {
  aiError.value = null
  initialStartFailed.value = false
  await connectAIStream(`/api/v1/sessions/${sessionId.value}/start`, {}, { initial: true })
}

// ── 打字机 ──────────────────────────────────────

// 模型把 reply 限制在 60 字内，几十毫秒就发完了：收到就 append 的话肉眼看不出逐字
// 效果，看起来仍像一次性弹出。所以把 chunk 排进队列，按固定节奏刷出。
const TYPE_TICK_MS = 30
// 整段播完的目标时长，长回复不至于拖太久
const TYPE_TARGET_MS = 1200
let typeQueue = []
let typeTimer = null

function stopTypewriter() {
  if (typeTimer !== null) {
    window.clearInterval(typeTimer)
    typeTimer = null
  }
}

/** 把还没播完的字一次补齐：卡片不能抢在正文前面出现，结束时也不能漏字。 */
function flushTypewriter() {
  stopTypewriter()
  if (typeQueue.length) {
    sessionStore.appendToLastMessage(typeQueue.join(''))
    typeQueue = []
  }
}

function enqueueText(chunk) {
  if (!chunk) return
  typeQueue.push(...chunk)
  if (typeTimer !== null) return
  typeTimer = window.setInterval(() => {
    if (!typeQueue.length) {
      stopTypewriter()
      return
    }
    // 每拍刷出足够多的字，让整段在目标时长内播完
    const perTick = Math.max(1, Math.ceil(typeQueue.length / (TYPE_TARGET_MS / TYPE_TICK_MS)))
    sessionStore.appendToLastMessage(typeQueue.splice(0, perTick).join(''))
    scrollToBottom()
  }, TYPE_TICK_MS)
}

async function connectAIStream(url, body, { initial = false } = {}) {
  if (sseActive.value) return
  sseActive.value = true
  const client = new SSEClient(url, {
    onText: (chunk) => {
      enqueueText(chunk)
    },
    onQuestion: (data) => {
      flushTypewriter()
      sessionStore.applyBriefEvent(data.brief)
      // 结构化数据挂到刚流式展示完的那条气泡上，不另起一条，避免同一句出现两次
      sessionStore.addStructuredMessage('question', data)
      scrollToBottom()
    },
    onConfirm: (data) => {
      flushTypewriter()
      sessionStore.applyBriefEvent(data.brief)
      sessionStore.addStructuredMessage('confirm', data)
      scrollToBottom()
    },
    onDone: () => {
      flushTypewriter()
      sseActive.value = false
      if (initial) initialStartFailed.value = false
    },
    onError: (err) => {
      console.error('SSE 错误:', err)
      flushTypewriter()
      sseActive.value = false
      aiError.value = {
        message: '对话连接中断',
        suggested_action: '请检查网络后重试，本次输入已保留。',
      }
      if (initial) initialStartFailed.value = true
    },
    onServiceError: (details) => {
      flushTypewriter()
      sseActive.value = false
      aiError.value = details
      if (initial) initialStartFailed.value = true
    },
  })

  sessionStore.setSSEClient(client)

  try {
    await client.connect(body)
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
    if (projectId.value && brief.value?.status !== 'confirmed') {
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
  router.push('/blueprint')
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
  // 离开页面时停掉打字机，否则定时器会继续往已清空的会话里追加
  stopTypewriter()
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
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
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
  .chat-page {
    height: auto;
    min-height: 100%;
    overflow: visible;
  }

  .chat-workspace {
    display: block;
    overflow: visible;
  }

  .chat-main {
    min-height: 560px;
    overflow: visible;
  }
}
</style>
