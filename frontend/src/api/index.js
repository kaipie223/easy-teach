import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ── 对话 ──────────────────────────────────────────

export function sendChatMessage(sessionId, message, history = []) {
  return api.post('/chat/send', { session_id: sessionId, message, history })
}

// ── 上传 ──────────────────────────────────────────

export function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return api.post('/upload/file', form)
}

// ── 语音 ──────────────────────────────────────────

export function transcribeAudio(audioBlob) {
  const form = new FormData()
  form.append('audio', audioBlob)
  return api.post('/speech/transcribe', form)
}

// ── 生成 ──────────────────────────────────────────

export function startGeneration(payload) {
  return api.post('/generate/start', payload)
}

export function getTaskStatus(taskId) {
  return api.get(`/generate/status/${taskId}`)
}

export function submitFeedback(taskId, feedback) {
  return api.post('/generate/feedback', { task_id: taskId, feedback })
}

// ── 导出 ──────────────────────────────────────────

export function downloadFile(fileId) {
  return api.get(`/export/download/${fileId}`, { responseType: 'blob' })
}
