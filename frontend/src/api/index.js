import axios from 'axios'

export const ACCESS_TOKEN_KEY = 'easy_teach_access_token'
export const USER_KEY = 'easy_teach_user'

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function saveAuthSession(payload) {
  localStorage.setItem(ACCESS_TOKEN_KEY, payload.access_token)
  localStorage.setItem(USER_KEY, JSON.stringify(payload.user))
}

export function clearAuthSession() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const path = error.config?.url || ''
    if (error.response?.status === 401 && !path.includes('/auth/')) {
      clearAuthSession()
      window.dispatchEvent(new CustomEvent('easy-teach-auth-expired'))
    }
    return Promise.reject(error)
  },
)

export default api

// ── 会话 ──────────────────────────────────────────

/** 创建新会话 */
export function createSession(courseName, projectId = null) {
  return api.post('/sessions', {
    teacher_name: '',
    subject: courseName,
    ...(projectId ? { project_id: projectId } : {}),
  })
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
export function uploadFile(file, sessionId, refDescription = '', options = {}) {
  const form = new FormData()
  form.append('file', file)
  form.append('session_id', sessionId)
  if (refDescription) form.append('ref_description', refDescription)
  return api.post('/upload', form, {
    ...options,
  })
}

// ── 语音 ──────────────────────────────────────────

/** 语音转文字 */
export function transcribeAudio(audioBlob, sessionId = null) {
  const form = new FormData()
  form.append('audio', audioBlob)
  if (sessionId) form.append('session_id', sessionId)
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

// ── 认证 ──────────────────────────────────────────

export function registerAccount(payload) {
  return api.post('/auth/register', payload)
}

export function loginAccount(payload) {
  return api.post('/auth/login', payload)
}

export function fetchCurrentUser() {
  return api.get('/auth/me')
}

// ── 项目 ──────────────────────────────────────────

export function fetchProjects(includeDeleted = false) {
  return api.get('/projects', { params: { include_deleted: includeDeleted } })
}

export function fetchProject(projectId, includeDeleted = false) {
  return api.get(`/projects/${projectId}`, { params: { include_deleted: includeDeleted } })
}

export function createProject(payload) {
  return api.post('/projects', payload)
}

export function updateProject(projectId, payload) {
  return api.patch(`/projects/${projectId}`, payload)
}

export function deleteProject(projectId) {
  return api.delete(`/projects/${projectId}`)
}

export function restoreProject(projectId) {
  return api.post(`/projects/${projectId}/restore`)
}

export function fetchBrief(projectId) {
  return api.get(`/projects/${projectId}/brief`)
}

export function updateBrief(projectId, payload) {
  return api.patch(`/projects/${projectId}/brief`, payload)
}

export function confirmBrief(projectId, expectedVersion = null) {
  const body = expectedVersion ? { expected_version: expectedVersion } : undefined
  return api.post(`/projects/${projectId}/brief/confirm`, body)
}
