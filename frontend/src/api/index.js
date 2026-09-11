import axios from 'axios'

export const ACCESS_TOKEN_KEY = 'easy_teach_access_token'
export const USER_KEY = 'easy_teach_user'
export const ACTIVE_PROJECT_KEY = 'active_project_id'

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
  localStorage.removeItem(ACTIVE_PROJECT_KEY)
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

export async function getApiErrorMessage(error, fallback = '请求失败，请重试') {
  const payload = error.response?.data
  if (typeof Blob !== 'undefined' && payload instanceof Blob) {
    try {
      const parsed = JSON.parse(await payload.text())
      return parsed?.error?.message || fallback
    } catch {
      return fallback
    }
  }
  return payload?.error?.message || error.message || fallback
}

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

// ── 管理员 ────────────────────────────────────────

export function fetchAdminUsers() {
  return api.get('/admin/users')
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

// ── 教学蓝图与成果生成 ─────────────────────────────

export function fetchCoursewarePlan(projectId) {
  return api.get(`/projects/${projectId}/plan`)
}

export function buildCoursewarePlan(projectId, options = {}) {
  return api.post(`/projects/${projectId}/plan`, {
    force_rebuild: Boolean(options.forceRebuild),
    generation_mode: options.generationMode || 'ai',
    allow_template_fallback: Boolean(options.allowTemplateFallback),
  }, {
    timeout: 10 * 60 * 1000,
  })
}

export function saveCoursewarePlanRevision(projectId, basePlanId, content, summary = '') {
  return api.post(`/projects/${projectId}/plan/revisions`, {
    base_plan_id: basePlanId,
    content,
    summary: summary || '教师编辑教学蓝图',
  })
}

export function startProjectGeneration(projectId, planId = null) {
  return api.post(`/projects/${projectId}/generate`, {
    ...(planId ? { plan_id: planId } : {}),
    idempotency_key: `project-generation:${projectId}:${planId || 'latest'}`,
  })
}

// ── 版本与局部修改 ──────────────────────────────────

export function fetchArtifactVersions(projectId) {
  return api.get(`/projects/${projectId}/versions`)
}

export function fetchArtifactVersion(projectId, versionId) {
  return api.get(`/projects/${projectId}/versions/${versionId}`)
}

export function interpretRevision(projectId, instruction, baseVersionId = null) {
  return api.post(`/projects/${projectId}/revisions/interpret`, {
    instruction,
    ...(baseVersionId ? { base_version_id: baseVersionId } : {}),
  })
}

export function applyRevision(projectId, patchId, confirmed = false) {
  return api.post(`/projects/${projectId}/revisions/apply`, {
    patch_id: patchId,
    confirmed,
  })
}

export function regenerateArtifactTarget(projectId, payload) {
  return api.post(`/projects/${projectId}/revisions/regenerate`, payload, {
    timeout: 2 * 60 * 1000,
  })
}

export function restoreArtifactVersion(projectId, versionId, summary = '') {
  return api.post(`/projects/${projectId}/versions/${versionId}/restore`, summary ? { summary } : undefined)
}

export function createVersionExports(projectId, artifactVersionId, formats = ['pptx', 'docx', 'pdf', 'html'], force = false) {
  return api.post(`/projects/${projectId}/exports`, {
    artifact_version_id: artifactVersionId,
    formats,
    force,
  }, { timeout: 30000 })
}

export function fetchProjectExports(projectId, artifactVersionId = null) {
  return api.get(`/projects/${projectId}/exports`, {
    params: artifactVersionId ? { artifact_version_id: artifactVersionId } : {},
  })
}

export function fetchExport(exportId) {
  return api.get(`/exports/${exportId}`)
}

export function downloadExport(exportId) {
  return api.get(`/exports/${exportId}/download`, { responseType: 'blob' })
}

// ── 项目资料 ──────────────────────────────────────

export function fetchProjectMaterials(projectId) {
  return api.get(`/projects/${projectId}/materials`)
}

export function uploadProjectMaterial(projectId, file, sessionId = null, refDescription = '', options = {}) {
  const form = new FormData()
  form.append('file', file)
  if (sessionId) form.append('session_id', sessionId)
  if (refDescription) form.append('ref_description', refDescription)
  return api.post(`/projects/${projectId}/materials`, form, options)
}

export function fetchMaterial(materialId) {
  return api.get(`/materials/${materialId}`)
}

export function fetchMaterialAnalysis(materialId) {
  return api.get(`/materials/${materialId}/analysis`)
}

export function fetchMaterialEvidence(materialId) {
  return api.get(`/materials/${materialId}/evidence`)
}

export function fetchMaterialBindings(materialId) {
  return api.get(`/materials/${materialId}/bindings`)
}

export function replaceMaterialBindings(materialId, bindings) {
  return api.put(`/materials/${materialId}/bindings`, { bindings })
}

export function downloadMaterial(materialId) {
  return api.get(`/materials/${materialId}/download`, { responseType: 'blob' })
}

export function deleteMaterial(materialId) {
  return api.delete(`/materials/${materialId}`)
}

// ── 知识库 ────────────────────────────────────────

export function fetchKnowledgeDocuments(params = {}) {
  return api.get('/knowledge/documents', { params })
}

export function uploadKnowledgeDocument(file, payload = {}, options = {}) {
  const form = new FormData()
  form.append('file', file)
  if (payload.collectionId) form.append('collection_id', payload.collectionId)
  if (payload.title) form.append('title', payload.title)
  form.append('enabled', String(Boolean(payload.enabled)))
  return api.post('/knowledge/documents', form, options)
}

export function updateKnowledgeDocument(documentId, payload) {
  return api.patch(`/knowledge/documents/${documentId}`, payload)
}

export function deleteKnowledgeDocument(documentId) {
  return api.delete(`/knowledge/documents/${documentId}`)
}

export function rebuildKnowledgeIndex() {
  return api.post('/knowledge/index', undefined, { timeout: 10 * 60 * 1000 })
}

export function indexKnowledgeDocument(documentId) {
  return api.post(`/knowledge/documents/${documentId}/index`, undefined, { timeout: 10 * 60 * 1000 })
}

export function searchKnowledge(query, topK = 5) {
  return api.post('/knowledge/search', { query, top_k: topK })
}
