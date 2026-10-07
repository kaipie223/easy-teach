<template>
  <div class="chat-page">
    <div v-if="loading" class="chat-status">
      <el-skeleton :rows="4" animated />
    </div>

    <div v-else-if="error && !messages.length" class="chat-status">
      <el-alert :title="error" type="error" show-icon :closable="false">
        <template #default>
          <el-button size="small" type="primary" @click="loadSession">重试</el-button>
        </template>
      </el-alert>
    </div>

    <template v-else>
      <header class="chat-bar">
        <div class="bar-context">
          <p class="eyebrow">需求共创</p>
          <h1>{{ projectTitle }}</h1>
        </div>

        <div class="bar-actions">
          <el-button
            class="brief-toggle"
            text
            :aria-expanded="briefOpen"
            aria-controls="brief-panel"
            @click="briefOpen = !briefOpen"
          >
            <el-icon><Tickets /></el-icon>
            需求单
          </el-button>

          <el-button
            type="primary"
            :disabled="sseActive || !canGenerate"
            @click="handleGenerate"
          >
            <el-icon><MagicStick /></el-icon>
            去生成成果
          </el-button>
        </div>
      </header>

      <div class="chat-workspace">
        <section class="chat-main">
          <div ref="msgListRef" class="chat-messages">
            <el-alert
              v-if="aiError"
              :title="aiError.message"
              :description="aiError.suggested_action"
              type="error"
              show-icon
              closable
              class="chat-alert"
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

            <div class="message-column">
              <div v-if="!messages.length" class="chat-starter">
                <span class="starter-mark" aria-hidden="true">
                  <el-icon><ChatDotRound /></el-icon>
                </span>
                <h2>聊聊这节课</h2>
                <p>说说学段、课时和你最在意的难点，我会边问边把需求单填起来。</p>
              </div>

              <template v-for="(msg, idx) in messages" :key="msg.id">
                <MessageBubble
                  v-if="msg.content"
                  :role="msg.role"
                  :content="msg.content"
                  :timestamp="msg.timestamp"
                  :typing="idx === messages.length - 1 && msg.role === 'assistant' && sseActive"
                />
                <QuestionCard
                  v-if="msg.type === 'question'"
                  :data="msg.data"
                  :prompt="cardPrompt(msg)"
                  @submit="answer => handleAnswerQuestion(answer)"
                  @skip="handleSkipQuestion"
                />
                <ConfirmPanel
                  v-else-if="msg.type === 'confirm'"
                  :data="msg.data"
                  @confirm="handleConfirm"
                  @modify="handleModify"
                />
              </template>
            </div>
          </div>

          <div class="chat-footer">
            <div class="composer">
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
              <p class="chat-hint">
                Enter 发送 · Shift+Enter 换行
                <template v-if="!canGenerate"> · 确认需求单后即可生成成果</template>
              </p>
            </div>
          </div>
        </section>

        <aside
          id="brief-panel"
          class="chat-aside"
          :class="{ 'is-open': briefOpen }"
          aria-label="需求确认单"
        >
          <TeachingBriefPanel
            v-if="projectId"
            :brief="sessionStore.brief"
            :saving="briefSaving"
            :confirming="briefConfirming"
            :active-field="activeField"
            @save="handleBriefSave"
            @confirm="handleConfirm"
          />
        </aside>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, MagicStick, Tickets } from '@element-plus/icons-vue'
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
// 当前追问的字段名：传给需求单面板，把卡片和右侧表单对应起来
const activeField = ref('')
const aiError = ref(null)
const initialStartFailed = ref(false)
// 窄屏默认收起需求单（宽屏由 CSS 常显，这个开关只影响窄屏）
const briefOpen = ref(false)

const sessionId = ref(route.params.sessionId)
const { messages, projectId, brief } = storeToRefs(sessionStore)
const canGenerate = computed(() => !projectId.value || brief.value?.status === 'confirmed')
const projectTitle = computed(
  () => projectStore.activeProject?.title || sessionStore.brief?.course_name || '课程对话',
)

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
    // 刷新后恢复"当前正在问哪一项"：由历史里最后一个结构化消息决定（只有追问
    // 卡片带字段；如果它已是确认面板，就说明不再问某一项了）
    const lastStructured = [...sessionStore.messages].reverse().find(message => message.type)
    activeField.value =
      lastStructured?.type === 'question' ? lastStructured.data?.field || '' : ''
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

async function handleSend(text, extra = {}) {
  if (!text.trim() || sseActive.value || sending.value) return

  sending.value = true
  aiError.value = null

  // 1. 添加用户消息
  sessionStore.addMessage({ role: 'user', content: text })

  // 2. 滚动到底部
  await nextTick()
  scrollToBottom()

  sending.value = false
  await connectAIStream(`/api/v1/sessions/${sessionId.value}/chat`, { message: text, ...extra })
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
      // 候选答案已经渲染成按钮，正文里那几句就不再显示一遍
      sessionStore.stripLastMessageChoices(data.options)
      // 结构化数据挂到刚流式展示完的那条气泡上，不另起一条，避免同一句出现两次
      sessionStore.addStructuredMessage('question', data)
      // 需求单里把这一项标成"正在问"；入场邀请卡没有字段，就都不标
      activeField.value = data.field || ''
      scrollToBottom()
    },
    onConfirm: (data) => {
      flushTypewriter()
      sessionStore.applyBriefEvent(data.brief)
      sessionStore.addStructuredMessage('confirm', data)
      // 到确认这一步就不再"正在问某一项"了
      activeField.value = ''
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

/**
 * 追问卡片的文案要不要显示（null=显示卡片自带的话；''=隐藏，气泡已经说过）。
 *
 * - 气泡没正文：卡片自己把话说清楚；
 * - 气泡已经包含同一句（刷新后读历史就是这种：后端问题同时是气泡正文）：不重复；
 * - 开场邀请卡：气泡里是模型自己的问候，邀请语只存在于卡片上，必须显示 —— 否则
 *   "先说说这节课：…"这句谁都看不到。
 */
function cardPrompt(message) {
  const text = (message.content || '').trim()
  if (!text) return null
  const prompt = (message.data?.prompt || '').trim()
  if (prompt && text.includes(prompt)) return ''
  return message.data?.invite ? null : ''
}

function handleAnswerQuestion(answer) {
  // 用户回答追问 → 作为新消息发送
  const text = answer.freeText || answer.selected || ''
  if (text) {
    handleSend(text)
  }
}

function handleSkipQuestion(payload) {
  // 字段名结构化传给后端：它会把这个字段按默认处理、不再追问，需求单的进度也会当场
  // 往前走一格。之前只发"跳过"两个字，既看不出去哪了，那句话还会被当成一次回答
  // 写进需求单（"跳过"变成教学目标），下一轮再问同一项。
  const label = payload?.fieldLabel || '这一项'
  handleSend(`「${label}」先跳过，按默认处理。`, { skip_field: payload?.field || undefined })
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
  // 语音结果回填输入框：教师确认/修改后自己发送，识别错字不会直接进对话
  inputRef.value?.appendText?.(text)
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
  padding: var(--space-6) var(--space-6) 0;
}

.chat-status {
  width: 100%;
  max-width: var(--container-reading);
  margin: 0 auto;
  padding: var(--space-10) 0;
}

/* ── 顶部：只有上下文与唯一主行动 ───────────────────────── */
.chat-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--border-hairline);
}

.bar-context { min-width: 0; }

.bar-context h1 {
  margin-top: var(--space-1);
  overflow: hidden;
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* 需求单开关只在窄屏出现（宽屏常显） */
.brief-toggle { display: none; }

/* ── 工作区 ─────────────────────────────────────────────── */
.chat-workspace {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--space-5);
  padding-top: var(--space-5);
}

/* 会话区是浮在灰底上的白色面板：这样"页面背景 / 会话表面 / 消息"三层分得开，
   助手消息直接落在面板上，不再出现"白气泡叠白背景"糊成一片的问题。 */
.chat-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 var(--space-6);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-xl);
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-5) 0 var(--space-4);
}

/* 面板内的轻量引导（不再套一层卡片） */
.chat-starter {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-12) var(--space-4);
  text-align: center;
}

.starter-mark {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  margin-bottom: var(--space-2);
  border-radius: var(--radius-lg);
  background: var(--brand-50);
  color: var(--text-brand);
  font-size: var(--text-lg);
}

.chat-starter h2 {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.chat-starter p {
  max-width: 42ch;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

/* 对话列收窄到阅读宽度：满屏宽的消息读起来很累 */
.message-column {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  width: 100%;
  max-width: var(--container-reading);
  margin: 0 auto;
}

.chat-alert { margin-bottom: var(--space-4); }

.chat-footer {
  flex: 0 0 auto;
  padding: var(--space-4) 0 var(--space-6);
  border-top: 1px solid var(--border-hairline);
}

.composer {
  width: 100%;
  max-width: var(--container-reading);
  margin: 0 auto;
}

.chat-hint {
  margin-top: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  text-align: center;
}

/* ── 需求单侧栏 ─────────────────────────────────────────── */
.chat-aside {
  width: 320px;
  flex: 0 0 320px;
  min-height: 0;
  overflow-y: auto;
}

/* ── 窄屏：需求单折叠，页面自己滚动 ──────────────────────── */
@media (max-width: 1024px) {
  .chat-page {
    height: 100%;
    overflow-y: auto;
    padding: var(--space-4) var(--space-4) 0;
  }

  .chat-bar {
    align-items: center;
    position: sticky;
    top: 0;
    z-index: var(--z-sticky);
    margin: calc(-1 * var(--space-4)) calc(-1 * var(--space-4)) 0;
    padding: var(--space-3) var(--space-4);
    background: var(--bg-page);
  }

  .bar-context h1 { font-size: var(--text-md); }

  .brief-toggle { display: inline-flex; }

  .chat-workspace {
    flex-direction: column;
    gap: var(--space-4);
    padding-top: var(--space-4);
  }

  .chat-main { padding: 0 var(--space-4); }

  .chat-messages {
    flex: none;
    overflow: visible;
    padding-bottom: var(--space-2);
  }

  .chat-aside {
    display: none;
    width: 100%;
    flex: none;
    overflow: visible;
    order: -1;
  }

  .chat-aside.is-open { display: block; }

  .chat-footer { padding-bottom: var(--space-4); }
}

@media (max-width: 480px) {
  .chat-page { padding: var(--space-3) var(--space-3) 0; }

  .chat-bar {
    margin: calc(-1 * var(--space-3)) calc(-1 * var(--space-3)) 0;
    padding: var(--space-3);
  }

  .bar-actions :deep(.el-button) { padding: 0 var(--space-3); }
}
</style>
