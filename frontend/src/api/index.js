import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

export default api

// ── 会话 ──────────────────────────────────────────

/** 创建新会话 */
export function createSession(courseName) {
  return api.post('/sessions', { course_name: courseName })
}

/** 查询会话详情 */
export function fetchSession(sessionId) {
  return api.get(`/sessions/${sessionId}`)
}

// ── 对话 ──────────────────────────────────────────

/** 发送对话消息（非 SSE 方式，备用） */
export function sendChatMessage(sessionId, message, history = []) {
  return api.post(`/sessions/${sessionId}/chat`, { message, history })
}

// ── 上传 ──────────────────────────────────────────

/** 上传文件 */
export function uploadFile(file, refDescription = '', options = {}) {
  const form = new FormData()
  form.append('file', file)
  if (refDescription) form.append('ref_description', refDescription)
  return api.post('/files/upload', form, {
    ...options,
  })
}

// ── 语音 ──────────────────────────────────────────

/** 语音转文字 */
export function transcribeAudio(audioBlob) {
  const form = new FormData()
  form.append('audio', audioBlob)
  return api.post('/speech/transcribe', form)
}

// ── 生成 ──────────────────────────────────────────

/** 触发课件生成 */
export function startGeneration(payload) {
  return api.post('/generate', payload)
}

/** 轮询任务状态 */
export function getTaskStatus(taskId) {
  return api.get(`/tasks/${taskId}/status`)
}

/** 提交修改反馈 */
export function submitFeedback(taskId, feedback) {
  return api.post('/generate/feedback', { task_id: taskId, feedback })
}

// ── 导出 ──────────────────────────────────────────

/** 下载课件文件 */
export function downloadFile(fileId) {
  return api.get(`/download/${fileId}`, { responseType: 'blob' })
}
