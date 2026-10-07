<template>
  <div class="page-stack">
    <AppPageHeader
      title="资料中心"
      subtitle="上传参考资料，查看解析状态，并把证据片段绑定到教学流程。（当前教案见上方流程条）"
    />

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
          <el-button text type="danger" @click="loadCurrentProject()">重试</el-button>
        </div>
      </template>
    </el-alert>

    <AppEmptyState
      v-if="!loadingProjects && !selectedProjectId"
      :icon="FolderOpened"
      title="还没有选中的教案"
      description="资料是按教案归档的，先在工作台挑一个或新建一个。"
    >
      <el-button type="primary" @click="go('/')">返回工作台</el-button>
    </AppEmptyState>

    <template v-else>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>上传参考资料</h2>
            <p>PDF、Word、PPT 和视频会异步提取证据；图片当前仅保存原文件。</p>
          </div>
          <el-button :loading="loadingMaterials" text @click="loadCurrentProject()">
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
          accept=".pdf,.docx,.pptx,.png,.jpg,.jpeg,.gif,.bmp,.webp,.mp4,.mov,.avi,.mkv"
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
            <span>单文件不超过 50 MB；视频支持 MP4、MOV、AVI、MKV</span>
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
          title="视频上传后会排队解析；第一阶段只理解画面，不转写教师讲解音轨。"
          type="info"
          show-icon
          :closable="false"
          class="video-note"
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
        <AppEmptyState
          v-else-if="!materials.length"
          :icon="UploadFilled"
          title="这一版还没有参考资料"
          description="上传 PDF、Word 或 PPT，解析后会把可引用的证据片段挂到这节课上。"
        >
          <el-button type="primary" @click="openFilePicker">
            <el-icon><UploadFilled /></el-icon>
            上传第一份资料
          </el-button>
        </AppEmptyState>
        <ul v-else class="material-list motion-stagger">
          <li v-for="material in materials" :key="material.material_id" class="material-row">
            <span class="row-mark" :class="`is-${material.file_type}`" aria-hidden="true">
              <el-icon><component :is="fileTypeIcon(material.file_type)" /></el-icon>
            </span>

            <div class="material-main">
              <div class="material-heading">
                <strong class="material-name-text" :title="material.original_name">
                  {{ material.original_name }}
                </strong>
                <span :class="['status-pill', materialStatusClass(material.status)]">
                  {{ materialStatusLabel(material.status, material.file_type) }}
                </span>
              </div>

              <p class="material-meta">
                <span>{{ fileTypeLabel(material.file_type) }}</span>
                <span class="dot" aria-hidden="true">·</span>
                <span class="text-tabular">{{ formatBytes(material.size_bytes) }}</span>
              </p>

              <p v-if="material.error_message" class="material-error">
                {{ material.error_message }}
              </p>

              <!-- 后端按资料类型给出真实阶段（图片多一步视觉识别，视频多一步转录解析） -->
              <StageProgress
                v-if="materialIsParsing(material)"
                class="material-progress"
                :percent="material.stage_percent"
                :label="material.stage_label || materialStatusLabel(material.status, material.file_type)"
                :steps="material.stages"
                :current="material.stage"
                :started-at="material.stage_started_at"
              />
              <!-- 只在"确实在推进"时才画进度条：失败态给满格进度条会与状态胶囊自相矛盾 -->
              <div v-else-if="materialIsInFlight(material)" class="progress-track">
                <div
                  :class="['progress-fill', material.status === 'processing' ? 'loading' : '']"
                  :style="{ width: `${materialProgress(material)}%` }"
                />
              </div>

              <!-- 用途与作用范围：原本各占一列，窄屏必然横向滚动，改成行内两组控件 -->
              <div class="material-bindings">
                <label class="binding-field">
                  <span class="binding-label">用途</span>
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
                </label>
                <label class="binding-field">
                  <span class="binding-label">作用范围</span>
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
                </label>
              </div>
            </div>

            <div class="row-actions">
              <el-button text @click="showDetails(material)">
                <el-icon><View /></el-icon>
                详情
              </el-button>
              <!-- 删除是破坏性操作：收进"更多"，避免与常规操作并排被误点 -->
              <el-dropdown trigger="click" @command="command => handleRowCommand(command, material)">
                <el-button text aria-label="更多操作">
                  <el-icon><MoreFilled /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="source">
                      <el-icon><Download /></el-icon>
                      打开原文件
                    </el-dropdown-item>
                    <el-dropdown-item divided command="delete">
                      <span class="danger-item">删除资料</span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </li>
        </ul>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>证据片段</h2>
            <p>PDF 页码、PPT 页码和其他定位信息会随证据一起保存。</p>
          </div>
          <span class="section-context">{{ evidenceSnips.length }} 条有效证据</span>
        </div>
        <p v-if="!evidenceSnips.length" class="list-hint">
          解析完成后，可引用的证据片段会显示在这里。
        </p>
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
        <p v-else class="list-hint">这份资料还没有解析详情。</p>
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
  Document,
  Download,
  FolderOpened,
  Monitor,
  MoreFilled,
  Picture,
  Refresh,
  UploadFilled,
  VideoCamera,
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
import StageProgress from '@/components/progress/StageProgress.vue'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import { SSEClient } from '@/utils/sse'
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
const isUnmounted = ref(false)
let progressStream = null
let reconnectTimer = null
let subscribedProjectId = null

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
  stopProgressStream()
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
    stopProgressStream()
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
    ensureProgressStream()
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
  if (
    isVideoFile(file)
    && materials.value.some(material => (
      material.file_type === 'video'
      && material.original_name === file.name
      && Number(material.size_bytes) === Number(file.size)
      && material.status !== 'archived'
    ))
  ) {
    uploadError.value = '同一视频已在当前项目中，请查看现有的排队或解析状态。'
    return
  }
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
    if (isVideoFile(file)) {
      ElMessage.success('视频已上传并进入解析队列；当前仅分析画面，不包含音轨讲解')
    } else if (isImageFile(file)) {
      ElMessage.success('图片已保存，视觉识别暂未启用')
    } else {
      ElMessage.success('资料已上传并完成解析')
    }
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

/** 文件类型图标：与列表里的类型色标配合，让同类资料一眼可辨。 */
const FILE_TYPE_ICONS = {
  pdf: Document,
  word: Document,
  ppt: Monitor,
  image: Picture,
  video: VideoCamera,
}

function fileTypeIcon(fileType) {
  return FILE_TYPE_ICONS[fileType] || Document
}

/** 行内"更多"菜单：破坏性操作与常规操作分开。 */
function handleRowCommand(command, material) {
  if (command === 'source') openSource(material)
  if (command === 'delete') removeMaterial(material)
}

/** 这个猜测的百分比只在解析还没真正开始时兜底用；解析中一律用后端真实阶段。 */
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

/** 解析中：后端已经按资料类型给出了阶段，此时不该再用前端猜的百分比。 */
function materialIsParsing(material) {
  return material.status === 'processing' && Boolean(material.stage)
}

/** 还在推进中（上传完成但未出结果）：这时才有必要显示进度条。 */
function materialIsInFlight(material) {
  return ['uploaded', 'queued', 'processing'].includes(material.status)
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
  if (locator.timestamp !== undefined) return `视频 ${locator.timestamp}`
  if (locator.offset !== undefined) return `文本位置 ${locator.offset}`
  return '来源定位'
}

/**
 * 订阅项目级进度流，替代原先 2.5 秒一次的整项目轮询。
 * 解析状态变化时再拉取一次完整资料（含绑定与证据），空闲时零请求。
 */
function ensureProgressStream() {
  if (isUnmounted.value || !selectedProjectId.value) return
  if (progressStream && subscribedProjectId === selectedProjectId.value) return
  stopProgressStream()
  subscribedProjectId = selectedProjectId.value
  progressStream = new SSEClient(
    `/api/v1/projects/${selectedProjectId.value}/events`,
    {
      onProgress: () => { loadCurrentProject({ silent: true }) },
      onError: () => { scheduleReconnect() },
      // 服务端在监听时长上限后会关闭长连接，这里自动重新订阅。
      onDone: () => { scheduleReconnect() },
    },
    // 该端点只提供 GET，用默认的 POST 会 405
    { method: 'GET' },
  )
  progressStream.connect()
}

function stopProgressStream() {
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  progressStream?.disconnect()
  progressStream = null
  subscribedProjectId = null
}

function scheduleReconnect() {
  if (isUnmounted.value || !selectedProjectId.value || reconnectTimer) return
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    ensureProgressStream()
  }, 3000)
}

function isImageFile(file) {
  return file?.type?.startsWith('image/') || /\.(png|jpe?g|gif|bmp|webp)$/i.test(file?.name || '')
}

function isVideoFile(file) {
  return file?.type?.startsWith('video/') || /\.(mp4|mov|avi|mkv)$/i.test(file?.name || '')
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
  return value === 100 ? '上传完成' : `${value}%`
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
  gap: var(--space-3);
}

.project-context-display {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.project-context-display span,
.upload-options label {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 700;
}

.project-context-display strong {
  max-width: 280px;
  overflow: hidden;
  color: var(--text-brand);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-context {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.upload-options {
  display: grid;
  gap: var(--space-2);
  max-width: 620px;
  margin-bottom: var(--space-3);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: calc(var(--space-1) * -1);
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.upload-zone {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  min-height: 102px;
  padding: var(--space-4) var(--space-5);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--neutral-25);
  color: var(--text-secondary);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.upload-zone:hover,
.upload-zone:focus-visible {
  border-color: var(--border-brand);
  background: var(--brand-50);
  outline: none;
}

.upload-zone.is-disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.upload-zone > div {
  display: grid;
  gap: var(--space-1);
  flex: 1;
}

.upload-zone strong {
  color: var(--text-primary);
}

.upload-zone span {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.upload-progress,
.video-note,
.upload-error {
  margin-top: var(--space-3);
}

/* ── 资料列表：列表行取代表格（7 列表格在窄屏只能横向滚动） ─── */
.material-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.material-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  gap: var(--space-4);
  align-items: start;
  padding: var(--space-4) var(--space-2);
  border-bottom: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  transition: background-color var(--duration-fast) var(--ease-standard);
}

.material-row:last-child { border-bottom: 0; }

.material-row:hover { background: var(--neutral-50); }

/* 类型色标：只表示文件类型，不参与主色体系 */
.row-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-lg);
  background: var(--bg-surface-sunken);
  font-size: var(--text-lg);
}

.row-mark.is-pdf { color: var(--file-pdf); }
.row-mark.is-word { color: var(--file-word); }
.row-mark.is-ppt { color: var(--file-ppt); }
.row-mark.is-image { color: var(--file-image); }
.row-mark.is-video { color: var(--file-video); }

.material-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.material-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.material-name-text {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.material-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.dot { color: var(--neutral-400); }

.material-error {
  color: var(--danger-600);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

/* 用途 / 作用范围：行内两组，窄屏自动换行 */
.material-bindings {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-5);
  margin-top: var(--space-1);
}

.binding-field {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.binding-label {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.usage-select { width: 190px; }

.scope-select { width: 130px; }

.row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  justify-content: flex-end;
}

.danger-item { color: var(--danger-600); }

.evidence-list {
  display: grid;
  gap: var(--space-2);
}

.evidence-card {
  display: grid;
  grid-template-columns: minmax(180px, 260px) minmax(0, 1fr) max-content;
  align-items: center;
  gap: var(--space-4);
  min-height: 104px;
  padding: var(--space-4) var(--space-4);
}

.evidence-meta {
  display: grid;
  align-self: start;
  gap: var(--space-2);
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
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-xs);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
  white-space: nowrap;
}

.evidence-card p {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.evidence-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  white-space: nowrap;
}

.detail-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.detail-summary > div {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
}

.detail-summary span {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.detail-summary strong {
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.analysis-content {
  margin-top: var(--space-4);
}

.analysis-content h3 {
  margin: 0 0 var(--space-2);
  color: var(--text-primary);
  font-size: var(--text-md);
}

.analysis-text {
  max-height: 220px;
  overflow: auto;
  padding: var(--space-3);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  line-height: 1.7;
  white-space: pre-wrap;
}

.analysis-content pre {
  max-height: 180px;
  overflow: auto;
  padding: var(--space-3);
  background: var(--bg-surface-sunken);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 768px) {
  /* 窄屏：类型标与文件名同排，操作换到下一行右对齐 */
  .material-row {
    grid-template-columns: 32px minmax(0, 1fr);
    grid-template-areas:
      'mark main'
      'mark actions';
    gap: var(--space-2) var(--space-3);
  }

  .row-mark {
    grid-area: mark;
    width: 32px;
    height: 32px;
    border-radius: var(--radius-md);
    font-size: var(--text-base);
  }

  .material-main { grid-area: main; }

  .row-actions {
    grid-area: actions;
    justify-content: flex-start;
  }

  .row-actions :deep(.el-button) {
    min-width: 36px;
    height: 36px;
  }

  /* 用途与范围在窄屏各占一行，避免两个下拉挤在一起 */
  .material-bindings {
    flex-direction: column;
    gap: var(--space-2);
  }

  .binding-field {
    justify-content: space-between;
    width: 100%;
  }

  .usage-select,
  .scope-select {
    flex: 1;
    width: auto;
    min-width: 0;
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
    gap: var(--space-2);
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
