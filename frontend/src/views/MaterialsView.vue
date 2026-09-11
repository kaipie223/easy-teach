<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>资料中心</h1>
        <p>上传参考资料，查看解析状态，并将证据片段绑定到教学生成流程。</p>
      </div>
      <div v-if="selectedProject" class="project-context-display">
        <span>当前项目</span>
        <strong>{{ selectedProject.title }}</strong>
      </div>
    </header>

    <el-alert
      v-if="pageError"
      type="error"
      show-icon
      :closable="false"
      class="page-alert"
    >
      <template #title>
        <div class="alert-content">
          <span>{{ pageError }}</span>
          <el-button text type="danger" @click="loadCurrentProject">重试</el-button>
        </div>
      </template>
    </el-alert>

    <el-empty v-if="!loadingProjects && !selectedProjectId" description="请先从工作台选择一个项目">
      <el-button type="primary" @click="go('/')">
        返回工作台
      </el-button>
    </el-empty>

    <template v-else>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>上传参考资料</h2>
            <p>PDF、Word 和 PPT 会提取证据；图片仅保存原文件，视觉与视频解析暂未启用。</p>
          </div>
          <el-button :loading="loadingMaterials" text @click="loadCurrentProject">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
        </div>

        <div class="upload-options">
          <label for="material-note">资料说明（可选）</label>
          <el-input
            id="material-note"
            v-model="uploadNote"
            maxlength="500"
            show-word-limit
            placeholder="例如：用于讲解核心概念和课堂案例"
            :disabled="uploading || !selectedProjectId"
          />
        </div>
        <input
          ref="fileInput"
          class="visually-hidden"
          type="file"
          accept=".pdf,.docx,.pptx,.png,.jpg,.jpeg,.gif,.bmp,.webp"
          @change="handleFileChange"
        />
        <div
          class="upload-zone"
          :class="{ 'is-disabled': uploading || !selectedProjectId }"
          role="button"
          tabindex="0"
          :aria-disabled="uploading || !selectedProjectId ? 'true' : 'false'"
          @click="openFilePicker"
          @keydown.enter.prevent="openFilePicker"
          @keydown.space.prevent="openFilePicker"
        >
          <el-icon :size="28"><UploadFilled /></el-icon>
          <div>
            <strong>{{ uploading ? '正在上传资料' : '选择要上传的资料' }}</strong>
            <span>单文件不超过 50 MB；当前支持 PDF、Word、PPT 和图片原文件</span>
          </div>
          <el-button type="primary" :loading="uploading" :disabled="!selectedProjectId">
            <el-icon><FolderOpened /></el-icon>
            选择文件
          </el-button>
        </div>
        <el-progress
          v-if="uploading"
          class="upload-progress"
          :percentage="uploadProgress"
          :format="progressFormat"
          :status="uploadProgress === 100 ? 'success' : undefined"
        />
        <el-alert
          v-if="uploadError"
          :title="uploadError"
          type="error"
          show-icon
          :closable="false"
          class="upload-error"
        />
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>资料列表与用途绑定</h2>
            <p>用途可以多选；修改后会立即保存到当前项目。</p>
          </div>
          <span v-if="selectedProject" class="section-context">{{ selectedProject.title }}</span>
        </div>

        <el-skeleton v-if="loadingMaterials" :rows="5" animated />
        <el-empty v-else-if="!materials.length" description="当前项目还没有参考资料">
          <el-button type="primary" @click="openFilePicker">
            <el-icon><UploadFilled /></el-icon>
            上传第一份资料
          </el-button>
        </el-empty>
        <div v-else class="table-wrap">
          <table class="data-table materials-table">
            <thead>
              <tr>
                <th>文件名</th>
                <th>类型</th>
                <th>大小</th>
                <th>解析状态</th>
                <th>资料用途</th>
                <th>作用范围</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="material in materials" :key="material.material_id">
                <td>
                  <span class="material-name" :title="material.original_name">
                    <el-icon><Document /></el-icon>
                    <span class="material-name-text">{{ material.original_name }}</span>
                  </span>
                  <span v-if="material.error_message" class="material-error">
                    {{ material.error_message }}
                  </span>
                </td>
                <td>{{ fileTypeLabel(material.file_type) }}</td>
                <td>{{ formatBytes(material.size_bytes) }}</td>
                <td>
                  <div class="progress-track">
                    <div
                      :class="['progress-fill', material.status === 'processing' ? 'loading' : '']"
                      :style="{ width: `${materialProgress(material)}%` }"
                    />
                  </div>
                  <span :class="['status-pill', materialStatusClass(material.status)]">
                    {{ materialStatusLabel(material.status, material.file_type) }}
                  </span>
                </td>
                <td>
                  <el-select
                    v-model="material.usageTypes"
                    class="usage-select"
                    size="small"
                    multiple
                    collapse-tags
                    collapse-tags-tooltip
                    :max-collapse-tags="1"
                    :disabled="material.savingBindings || material.status === 'archived'"
                    placeholder="选择用途"
                    @change="saveBindings(material)"
                  >
                    <el-option
                      v-for="option in usageOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </td>
                <td>
                  <el-select
                    v-model="material.targetType"
                    class="scope-select"
                    size="small"
                    :disabled="material.savingBindings || !material.usageTypes.length"
                    @change="saveBindings(material)"
                  >
                    <el-option
                      v-for="option in targetOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </td>
                <td class="link-actions material-actions">
                  <el-button link type="primary" @click="showDetails(material)">
                    <el-icon><View /></el-icon>
                    详情
                  </el-button>
                  <el-button link type="primary" @click="openSource(material)">
                    <el-icon><Download /></el-icon>
                    原文件
                  </el-button>
                  <el-button link type="danger" @click="removeMaterial(material)">
                    <el-icon><Delete /></el-icon>
                    删除
                  </el-button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>证据片段</h2>
            <p>PDF 页码、PPT 页码和其他定位信息会随证据一起保存。</p>
          </div>
          <span class="section-context">{{ evidenceSnips.length }} 条有效证据</span>
        </div>
        <el-empty v-if="!evidenceSnips.length" description="解析完成后，证据片段会显示在这里" />
        <div v-else class="evidence-list">
          <article v-for="evidence in evidenceSnips" :key="evidence.evidence_id" class="evidence-card">
            <div class="evidence-meta">
              <strong>{{ evidence.materialName }}</strong>
              <span class="locator-badge">{{ locatorLabel(evidence.locator_json) }}</span>
            </div>
            <p>{{ evidence.text }}</p>
            <div class="evidence-actions">
              <el-button size="small" link type="primary" @click="showDetails(evidence.material)">
                查看解析
              </el-button>
              <el-button size="small" link @click="openSource(evidence.material)">
                <el-icon><Download /></el-icon>
                打开来源
              </el-button>
            </div>
          </article>
        </div>
      </section>
    </template>

    <el-dialog
      v-model="detailsVisible"
      :title="selectedMaterial?.original_name || '资料详情'"
      width="min(680px, calc(100vw - 32px))"
    >
      <template v-if="selectedMaterial">
        <div class="detail-summary">
          <div>
            <span>解析状态</span>
            <strong :class="['status-pill', materialStatusClass(selectedMaterial.status)]">
              {{ materialStatusLabel(selectedMaterial.status, selectedMaterial.file_type) }}
            </strong>
          </div>
          <div>
            <span>上传时间</span>
            <strong>{{ formatDate(selectedMaterial.created_at) }}</strong>
          </div>
          <div>
            <span>证据数量</span>
            <strong>{{ selectedMaterial.evidence?.length || 0 }}</strong>
          </div>
        </div>
        <el-alert
          v-if="selectedMaterial.error_message"
          :title="selectedMaterial.error_message"
          type="error"
          show-icon
          :closable="false"
        />
        <div v-if="selectedMaterial.analysis" class="analysis-content">
          <h3>解析结果</h3>
          <p v-if="selectedMaterial.analysis.text_content" class="analysis-text">
            {{ selectedMaterial.analysis.text_content }}
          </p>
          <pre>{{ JSON.stringify(selectedMaterial.analysis.result_json || {}, null, 2) }}</pre>
        </div>
        <el-empty v-else description="暂无解析详情" />
      </template>
      <template #footer>
        <el-button @click="detailsVisible = false">关闭</el-button>
        <el-button
          v-if="selectedMaterial"
          type="primary"
          :disabled="selectedMaterial.status === 'archived'"
          @click="openSource(selectedMaterial)"
        >
          <el-icon><Download /></el-icon>
          打开原文件
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Document,
  Download,
  FolderOpened,
  Refresh,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'
import {
  deleteMaterial,
  downloadMaterial,
  fetchMaterialAnalysis,
  fetchMaterialBindings,
  fetchMaterialEvidence,
  fetchProjectMaterials,
  replaceMaterialBindings,
  uploadProjectMaterial,
} from '@/api'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const materials = ref([])
const loadingProjects = ref(true)
const loadingMaterials = ref(false)
const pageError = ref('')
const uploadError = ref('')
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadNote = ref('')
const fileInput = ref(null)
const detailsVisible = ref(false)
const selectedMaterial = ref(null)
const pollTimer = ref(null)
const isUnmounted = ref(false)

const usageOptions = [
  { value: 'content_basis', label: '内容依据' },
  { value: 'knowledge_structure', label: '知识结构参考' },
  { value: 'case_source', label: '案例来源' },
  { value: 'visual_style', label: '排版或视觉风格' },
  { value: 'interaction_asset', label: '互动素材' },
  { value: 'archive_only', label: '仅存档' },
]

const targetOptions = [
  { value: 'whole_course', label: '整节课' },
  { value: 'knowledge_point', label: '某个知识点' },
  { value: 'slide_type', label: '某一页类型' },
  { value: 'lesson_section', label: '教案章节' },
]

const selectedProjectId = computed(() => projectStore.activeProjectId)
const selectedProject = computed(() => projectStore.activeProject)

const evidenceSnips = computed(() =>
  materials.value.flatMap(material =>
    (material.evidence || []).map(evidence => ({
      ...evidence,
      material,
      materialName: material.original_name,
    })),
  ),
)

onMounted(loadProjectContext)
onBeforeUnmount(() => {
  isUnmounted.value = true
  clearMaterialPolling()
})

async function loadProjectContext() {
  loadingProjects.value = true
  pageError.value = ''
  try {
    await projectStore.ensureActiveProject()
    await loadCurrentProject()
  } catch (error) {
    pageError.value = apiErrorMessage(error, '项目加载失败，请重试')
  } finally {
    loadingProjects.value = false
  }
}

async function loadCurrentProject(options = {}) {
  const silent = Boolean(options.silent)
  if (!selectedProjectId.value) {
    clearMaterialPolling()
    materials.value = []
    return
  }
  if (!silent) loadingMaterials.value = true
  pageError.value = ''
  try {
    const response = await fetchProjectMaterials(selectedProjectId.value)
    const rows = response.data || []
    materials.value = await Promise.all(rows.map(enrichMaterial))
  } catch (error) {
    pageError.value = apiErrorMessage(error, '资料加载失败，请重试')
    materials.value = []
  } finally {
    if (!silent) loadingMaterials.value = false
    scheduleMaterialPolling()
  }
}

async function enrichMaterial(material) {
  const [bindingsResult, evidenceResult] = await Promise.allSettled([
    fetchMaterialBindings(material.material_id),
    fetchMaterialEvidence(material.material_id),
  ])
  const bindings = bindingsResult.status === 'fulfilled' ? bindingsResult.value.data || [] : []
  const evidence = evidenceResult.status === 'fulfilled' ? evidenceResult.value.data || [] : []
  return {
    ...material,
    bindings,
    evidence,
    usageTypes: bindings.map(binding => binding.usage_type),
    savedUsageTypes: bindings.map(binding => binding.usage_type),
    targetType: bindings[0]?.target_type || 'whole_course',
    savedTargetType: bindings[0]?.target_type || 'whole_course',
    savingBindings: false,
  }
}

function openFilePicker() {
  if (!selectedProjectId.value || uploading.value) return
  fileInput.value?.click()
}

async function handleFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  await uploadMaterial(file)
}

async function uploadMaterial(file) {
  if (!selectedProjectId.value || uploading.value) return
  uploadError.value = ''
  uploading.value = true
  uploadProgress.value = 0
  try {
    const activeProjectId = sessionStore.projectId?.value ?? sessionStore.projectId
    const activeSessionId = sessionStore.sessionId?.value ?? sessionStore.sessionId
    const sessionId = activeProjectId === selectedProjectId.value
      ? activeSessionId || null
      : null
    await uploadProjectMaterial(
      selectedProjectId.value,
      file,
      sessionId,
      uploadNote.value.trim(),
      {
        onUploadProgress: event => {
          if (event.total) {
            uploadProgress.value = Math.min(95, Math.round((event.loaded / event.total) * 95))
          }
        },
      },
    )
    uploadProgress.value = 100
    uploadNote.value = ''
    await loadCurrentProject()
    ElMessage.success(
      isImageFile(file) ? '图片已保存，视觉识别暂未启用' : '资料已上传并完成解析',
    )
  } catch (error) {
    uploadError.value = apiErrorMessage(error, '资料上传失败，请检查文件后重试')
    uploadProgress.value = 0
  } finally {
    uploading.value = false
  }
}

async function saveBindings(material) {
  if (material.savingBindings || !material.material_id) return
  const nextUsageTypes = [...material.usageTypes]
  const previousUsageTypes = [...material.savedUsageTypes]
  const previousTargetType = material.savedTargetType
  material.savingBindings = true
  try {
    const bindings = nextUsageTypes.map(usageType => ({
      usage_type: usageType,
      target_type: material.targetType,
      confirmed_by_teacher: true,
    }))
    const response = await replaceMaterialBindings(material.material_id, bindings)
    material.bindings = response.data || []
    material.savedUsageTypes = nextUsageTypes
    material.savedTargetType = material.targetType
    ElMessage.success('资料用途已保存')
  } catch (error) {
    material.usageTypes = previousUsageTypes
    material.targetType = previousTargetType
    ElMessage.error(apiErrorMessage(error, '用途保存失败，请重试'))
  } finally {
    material.savingBindings = false
  }
}

async function removeMaterial(material) {
  try {
    await ElMessageBox.confirm(
      `删除“${material.original_name}”？已绑定的证据也会停止用于后续生成。`,
      '删除资料',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteMaterial(material.material_id)
    await loadCurrentProject()
    ElMessage.success('资料已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(apiErrorMessage(error, '资料删除失败，请重试'))
    }
  }
}

async function showDetails(material) {
  selectedMaterial.value = material
  detailsVisible.value = true
  try {
    const response = await fetchMaterialAnalysis(material.material_id)
    selectedMaterial.value = { ...material, analysis: response.data }
  } catch (error) {
    selectedMaterial.value = { ...material, analysis: null }
    if (error.response?.status !== 404) {
      ElMessage.error(apiErrorMessage(error, '解析详情加载失败'))
    }
  }
}

async function openSource(material) {
  if (!material?.material_id || material.status === 'archived') return
  try {
    const response = await downloadMaterial(material.material_id)
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.target = '_blank'
    anchor.rel = 'noopener'
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '原文件打开失败，请重试'))
  }
}

function materialProgress(material) {
  return {
    uploaded: 15,
    queued: 25,
    processing: 60,
    ready: 100,
    failed: 100,
    archived: 100,
  }[material.status] || 0
}

function materialStatusLabel(status, fileType) {
  if (status === 'ready' && fileType === 'image') return '仅保存'
  return {
    uploaded: '已上传',
    queued: '排队中',
    processing: '解析中',
    ready: '已解析',
    failed: '解析失败',
    archived: '已删除',
  }[status] || status || '未知状态'
}

function materialStatusClass(status) {
  return {
    uploaded: 'pending',
    queued: 'pending',
    processing: 'running',
    ready: 'done',
    failed: 'failed',
    archived: 'draft',
  }[status] || 'pending'
}

function fileTypeLabel(fileType) {
  return {
    pdf: 'PDF',
    word: 'Word',
    ppt: 'PPT',
    image: '图片',
    video: '视频',
  }[fileType] || fileType
}

function locatorLabel(locator = {}) {
  if (locator.page) return `第 ${locator.page} 页`
  if (locator.slide) return `第 ${locator.slide} 页`
  if (locator.paragraph) return `第 ${locator.paragraph} 段`
  if (locator.table) return `第 ${locator.table} 个表格`
  if (locator.timestamp) return `视频 ${locator.timestamp}`
  if (locator.offset !== undefined) return `文本位置 ${locator.offset}`
  return '来源定位'
}

function scheduleMaterialPolling() {
  clearMaterialPolling()
  if (isUnmounted.value) return
  if (!materials.value.some(material => ['queued', 'processing'].includes(material.status))) return
  pollTimer.value = window.setTimeout(() => {
    if (isUnmounted.value) return
    loadCurrentProject({ silent: true })
  }, 2500)
}

function clearMaterialPolling() {
  if (!pollTimer.value) return
  window.clearTimeout(pollTimer.value)
  pollTimer.value = null
}

function isImageFile(file) {
  return file?.type?.startsWith('image/') || /\.(png|jpe?g|gif|bmp|webp)$/i.test(file?.name || '')
}

function formatBytes(value) {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

function formatDate(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '未记录'
    : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

function progressFormat(value) {
  return value === 100 ? '解析完成' : `${value}%`
}

function apiErrorMessage(error, fallback) {
  return error.response?.data?.error?.message || error.message || fallback
}

function go(path) {
  router.push(path)
}
</script>

<style scoped>
.page-alert {
  margin-bottom: 0;
}

.alert-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.project-context-display {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.project-context-display span,
.upload-options label {
  color: #475569;
  font-size: 13px;
  font-weight: 700;
}

.project-context-display strong {
  max-width: 280px;
  overflow: hidden;
  color: #1463ff;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-context {
  color: #64748b;
  font-size: 13px;
  white-space: nowrap;
}

.upload-options {
  display: grid;
  gap: 8px;
  max-width: 620px;
  margin-bottom: 14px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.upload-zone {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 102px;
  padding: 16px 20px;
  border: 1px dashed #9fb1c9;
  border-radius: 8px;
  background: #fbfdff;
  color: #334155;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.upload-zone:hover,
.upload-zone:focus-visible {
  border-color: #1463ff;
  background: #f5f9ff;
  outline: none;
}

.upload-zone.is-disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.upload-zone > div {
  display: grid;
  gap: 4px;
  flex: 1;
}

.upload-zone strong {
  color: #0f172a;
}

.upload-zone span {
  color: #64748b;
  font-size: 13px;
}

.upload-progress,
.upload-error {
  margin-top: 14px;
}

.materials-table {
  min-width: 1080px;
}

.material-name {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 220px;
  color: #0f172a;
  font-weight: 700;
}

.material-name-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.material-error {
  display: block;
  max-width: 240px;
  margin-top: 5px;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.4;
}

.usage-select {
  width: 190px;
}

.scope-select {
  width: 130px;
}

.material-actions {
  gap: 6px;
}

.material-actions .el-button {
  padding: 0 4px;
}

.evidence-list {
  display: grid;
  gap: 10px;
}

.evidence-card {
  display: grid;
  grid-template-columns: minmax(180px, 260px) minmax(0, 1fr) max-content;
  align-items: center;
  gap: 18px;
  min-height: 104px;
  padding: 16px 18px;
}

.evidence-meta {
  display: grid;
  align-self: start;
  gap: 8px;
}

.evidence-meta strong {
  min-width: 0;
  overflow: hidden;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.locator-badge {
  justify-self: start;
  flex: 0 0 auto;
  padding: 3px 7px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  color: #475569;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.evidence-card p {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: #475569;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.evidence-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  white-space: nowrap;
}

.detail-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.detail-summary > div {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.detail-summary span {
  color: #64748b;
  font-size: 13px;
}

.detail-summary strong {
  color: #0f172a;
  overflow-wrap: anywhere;
}

.analysis-content {
  margin-top: 18px;
}

.analysis-content h3 {
  margin: 0 0 8px;
  color: #0f172a;
  font-size: 16px;
}

.analysis-text {
  max-height: 220px;
  overflow: auto;
  padding: 12px;
  border: 1px solid #e4e9f2;
  border-radius: 6px;
  color: #334155;
  line-height: 1.7;
  white-space: pre-wrap;
}

.analysis-content pre {
  max-height: 180px;
  overflow: auto;
  padding: 12px;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 720px) {
  .project-context-display {
    width: 100%;
    min-width: 0;
    align-items: flex-start;
    flex-direction: column;
  }

  .upload-zone {
    align-items: flex-start;
    flex-direction: column;
  }

  .upload-zone .el-button {
    width: 100%;
  }

  .detail-summary {
    grid-template-columns: 1fr;
  }

  .evidence-card {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .evidence-actions {
    justify-content: flex-start;
  }
}

@media (prefers-reduced-motion: reduce) {
  .upload-zone {
    transition: none;
  }
}
</style>
