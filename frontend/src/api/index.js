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

/**
 * 请求校验失败（422）的可读化。
 *
 * 后端对这类错误的 message 是固定的英文 "Request validation failed."，真正的原因
 * 在 details 数组里（哪个字段、为什么）。不翻出来，用户只会看到一句没有信息量的
 * 提示，无法判断是哪个流程出了问题。
 */
const VALIDATION_REASON = {
  missing: '缺少必填字段',
  string_too_short: '长度不足',
  string_too_long: '超出长度限制',
  enum: '取值不在允许范围',
  value_error: '取值无效',
  json_invalid: '请求体不是合法 JSON',
}

function formatValidationError(payload) {
  const details = payload?.error?.details
  if (!Array.isArray(details) || !details.length) return null
  const items = details
    .map((item) => {
      const field = Array.isArray(item?.loc)
        ? item.loc.filter((part) => part !== 'body').join('.')
        : ''
      const reason = VALIDATION_REASON[item?.type] || item?.msg || '格式不正确'
      return field ? `${field} ${reason}` : reason
    })
    .slice(0, 3)
    .join('；')
  return items ? `请求参数校验失败：${items}` : null
}

export async function getApiErrorMessage(error, fallback = '请求失败，请重试') {
  let payload = error.response?.data
  if (typeof Blob !== 'undefined' && payload instanceof Blob) {
    try {
      payload = JSON.parse(await payload.text())
    } catch {
      return fallback
    }
  }
  if (payload?.error?.code === 'validation_error') {
    return formatValidationError(payload) || '请求参数校验失败，请刷新页面后重试'
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
export function transcribeAudio(audioBlob, sessionId = null, filename = 'voice.webm') {
  const form = new FormData()
  // 必须显式带文件名：后端按扩展名做白名单校验，而 FormData 默认的 "blob" 没有扩展名。
  form.append('audio', audioBlob, filename)
  if (sessionId) form.append('session_id', sessionId)
  // 本地 faster-whisper 在 CPU 上转写明显慢于普通接口，默认 30s 超时不够。
  return api.post('/speech/transcribe', form, { timeout: 120000 })
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

/**
 * 触发课件生成。
 *
 * 刻意不发送幂等键：只有服务端知道这次生成用的是哪份蓝图快照，客户端能给出的
 * 只有 "latest"，于是换蓝图之后仍会命中同一个旧任务，"重新生成"看起来毫无反应。
 * 服务端按 project + 本次基准版本派生幂等键，同一版本重复点击依然只跑一次。
 */
export function startProjectGeneration(projectId, planId = null) {
  return api.post(`/projects/${projectId}/generate`, {
    ...(planId ? { plan_id: planId } : {}),
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

/** 设置或移除某一页的配图，material_id 传 null 表示移除 */
export function setSlideImage(projectId, versionId, slideId, payload) {
  return api.put(`/projects/${projectId}/versions/${versionId}/slides/${slideId}/image`, payload)
}

/** 给这一版里还没有配图的页面批量生成插图：整批只产生一个新版本 */
export function illustrateVersion(projectId, versionId) {
  // 逐页生成插图，整批可能跑几分钟，超时给足
  return api.post(`/projects/${projectId}/versions/${versionId}/illustrate`, undefined, { timeout: 600000 })
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

export function generateSlideImage(projectId, prompt) {
  // 图像生成比普通接口慢（Seedream 单张约 10-40s），超时给足。
  return api.post(`/projects/${projectId}/images/generate`, { prompt }, { timeout: 180000 })
}

export function fetchProjectExports(projectId, artifactVersionId = null) {
  return api.get(`/projects/${projectId}/exports`, {
    params: artifactVersionId ? { artifact_version_id: artifactVersionId } : {},
  })
}

/** 某个成果版本已生成的产物文件（生成任务写出的 pptx/docx/html） */
export function fetchVersionFiles(projectId, artifactVersionId = null) {
  return api.get(`/projects/${projectId}/files`, {
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
