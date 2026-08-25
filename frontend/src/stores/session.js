import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api, {
  confirmBrief as confirmBriefRequest,
  createSession as createSessionRequest,
  fetchBrief as fetchBriefRequest,
  updateBrief as updateBriefRequest,
} from '@/api'

export const useSessionStore = defineStore('session', () => {
  // ── 状态 ──────────────────────────────────────
  const sessionId = ref(null)
  const projectId = ref(null)
  const brief = ref(null)
  const messages = ref([])
  const isLoading = ref(false)
  const error = ref(null)
  const currentSSEClient = ref(null) // 当前活跃的 SSE 连接

  // ── 计算属性 ──────────────────────────────────
  const messageCount = computed(() => messages.value.length)
  const lastMessage = computed(() => messages.value[messages.value.length - 1])
  const isActive = computed(() => !!sessionId.value)

  // ── 消息操作 ──────────────────────────────────

  /** 添加一条消息 */
  function addMessage(message) {
    const msg = {
      id: `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      timestamp: new Date().toISOString(),
      ...message,
    }
    messages.value.push(msg)
    return msg
  }

  /** 更新最后一条消息（用于打字机逐字追加） */
  function appendToLastMessage(textChunk) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content = (last.content || '') + textChunk
    } else {
      addMessage({ role: 'assistant', content: textChunk })
    }
  }

  /** 在对话末尾添加结构化数据（追问卡片 / 确认面板） */
  function addStructuredMessage(type, data) {
    const last = messages.value[messages.value.length - 1]
    // 如果最后一条已经是同类型的结构化消息，替换
    if (last && last.type === type) {
      last.data = data
    } else {
      addMessage({ role: 'assistant', type, data, content: '' })
    }
  }

  /** 清空消息列表 */
  function clearMessages() {
    messages.value = []
    error.value = null
  }

  // ── 会话操作 ──────────────────────────────────

  /** 创建新会话 */
  async function createSession(courseName, requestedProjectId = null) {
    isLoading.value = true
    error.value = null
    try {
      const res = await createSessionRequest(courseName, requestedProjectId)
      sessionId.value = res.data.session_id
      projectId.value = res.data.project_id || requestedProjectId
      brief.value = null
      clearMessages()
      return res.data
    } catch (err) {
      error.value = err.response?.data?.error?.message || err.message
      throw err
    } finally {
      isLoading.value = false
    }
  }

  /** 加载已有会话 */
  async function fetchSession(id) {
    isLoading.value = true
    error.value = null
    try {
      const res = await api.get(`/sessions/${id}`)
      sessionId.value = id
      projectId.value = res.data.project_id || null
      messages.value = (res.data.messages || []).map((message) => ({
        id: message.id,
        role: message.role,
        content: message.content,
        timestamp: message.created_at,
        type: ['question', 'confirm'].includes(message.msg_type) ? message.msg_type : undefined,
        data: message.event_data || undefined,
      }))
      return res.data
    } catch (err) {
      error.value = err.response?.data?.error?.message || err.message
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function fetchBrief(id = projectId.value) {
    if (!id) {
      brief.value = null
      return null
    }
    try {
      const res = await fetchBriefRequest(id)
      brief.value = res.data
      return res.data
    } catch (err) {
      if (err.response?.status === 404) {
        brief.value = null
        return null
      }
      throw err
    }
  }

  async function updateBrief(changes, id = projectId.value) {
    const res = await updateBriefRequest(id, changes)
    brief.value = res.data
    return res.data
  }

  async function confirmBrief(id = projectId.value) {
    const res = await confirmBriefRequest(id, brief.value?.version || null)
    brief.value = res.data
    return res.data
  }

  /** 重置会话状态 */
  function resetSession() {
    sessionId.value = null
    projectId.value = null
    brief.value = null
    messages.value = []
    isLoading.value = false
    error.value = null
    if (currentSSEClient.value) {
      currentSSEClient.value.disconnect()
      currentSSEClient.value = null
    }
  }

  /** 设置当前 SSE 客户端引用 */
  function setSSEClient(client) {
    currentSSEClient.value = client
  }

  return {
    // 状态
    sessionId,
    projectId,
    brief,
    messages,
    isLoading,
    error,
    currentSSEClient,
    // 计算属性
    messageCount,
    lastMessage,
    isActive,
    // 方法
    addMessage,
    appendToLastMessage,
    addStructuredMessage,
    clearMessages,
    createSession,
    fetchSession,
    fetchBrief,
    updateBrief,
    confirmBrief,
    resetSession,
    setSSEClient,
  }
})
