<template>
  <div class="page-stack">
    <AppPageHeader
      title="导出与版本"
      subtitle="选择具体成果版本导出，文件内容不会随当前版本变化。"
    >
      <template #actions>
        <el-button :loading="loading" @click="loadWorkspace">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </template>
    </AppPageHeader>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
    />

    <el-skeleton v-if="loading && !versions.length" :rows="6" animated />

    <AppEmptyState
      v-else-if="!projectId || !versions.length"
      :icon="Download"
      title="还没有可导出的成果"
      description="先在「教学蓝图」生成这一版成果，再回来导出 PPT、教案与互动内容。"
    >
      <el-button type="primary" @click="router.push('/blueprint')">去生成成果</el-button>
    </AppEmptyState>

    <template v-else>
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>导出这四个格式</h2>
            <p>PPTX 演示文稿 · DOCX 教案 · PDF 打印版 · HTML 互动内容</p>
          </div>
        </div>

        <div class="export-controls">
          <div class="control-field">
            <label class="field-label" for="export-version">成果版本</label>
            <el-select
              id="export-version"
              v-model="selectedVersionId"
              class="version-select"
              @change="loadExports"
            >
              <el-option
                v-for="version in versions"
                :key="version.artifact_version_id"
                :label="`v${version.version} · ${version.summary}`"
                :value="version.artifact_version_id"
              />
            </el-select>
          </div>

          <div class="export-actions">
            <!-- 必须显式加括号调用：@click="createExports" 会把 MouseEvent 当作
                 force 参数传进去，序列化后是 {}，后端校验 force: bool 直接 422。 -->
            <el-button type="primary" :loading="exporting" @click="createExports()">
              <el-icon><Download /></el-icon>
              导出全部格式
            </el-button>
            <el-button @click="openEditor">返回成果编辑</el-button>
          </div>
        </div>

        <p class="hint">
          重复导出会按当前渲染器重新生成一份（本地渲染，几秒）；同一格式正在渲染的任务不会重复排队。
        </p>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>导出记录</h2>
            <p v-if="selectedVersion">
              当前版本 v{{ selectedVersion.version }} · {{ selectedVersion.summary }}
            </p>
          </div>
        </div>

        <p v-if="!exports.length" class="list-hint">
          该版本还没有导出记录，点上面的「导出全部格式」创建。
        </p>

        <!-- 每个格式只占一行（最新一条），更早的收进可展开的「历史版本」：
             重复导出会不断产生记录，平铺出来会把列表撑成流水账。 -->
        <ul v-else class="export-list motion-stagger">
          <li v-for="group in groupedExports" :key="group.format" class="export-group">
            <div class="export-row">
              <span class="format-mark" :class="`is-${group.format}`" aria-hidden="true">
                <el-icon><component :is="formatIcon(group.format)" /></el-icon>
              </span>

              <div class="export-main">
                <div class="export-heading">
                  <strong>{{ formatLabel(group.format) }}</strong>
                  <span class="status-pill" :class="statusClass(group.latest.status)">
                    {{ statusLabel(group.latest.status) }}
                  </span>
                  <span v-if="versionLabel(group.latest.artifact_version_id)" class="version-chip">
                    {{ versionLabel(group.latest.artifact_version_id) }}
                  </span>
                </div>
                <p class="export-file">{{ group.latest.file_name || '文件生成中…' }}</p>
                <p class="export-meta">{{ metaText(group.latest) }}</p>
                <!-- 每条记录只负责一个格式，所以没有步骤清单，只有进度与文案 -->
                <StageProgress
                  v-if="isRunning(group.latest)"
                  class="export-progress"
                  :percent="group.latest.stage_percent"
                  :label="group.latest.stage_label || statusLabel(group.latest.status)"
                  :started-at="group.latest.started_at"
                />
              </div>

              <div class="row-actions">
                <!-- 四种格式都能预览：HTML/PDF 直接渲染真文件，PPT/教案按蓝图数据渲染内容预览 -->
                <el-button
                  v-if="group.latest.status === 'completed'"
                  @click="previewExport(group.latest)"
                >
                  <el-icon><View /></el-icon>
                  预览
                </el-button>
                <el-button
                  v-if="group.latest.status === 'completed'"
                  type="primary"
                  @click="download(group.latest)"
                >
                  下载
                </el-button>
                <!-- 「重新导出」只重做这一个格式（失败的记录也靠它重试）。后端现在
                     对已完成的导出本就会重新渲染，这个入口的价值是"只重排这一份"。 -->
                <el-button
                  v-if="group.latest.status === 'completed'"
                  @click="retryExport(group.latest)"
                >
                  重新导出
                </el-button>
                <el-button
                  v-else-if="group.latest.status === 'failed'"
                  @click="retryExport(group.latest)"
                >
                  重试
                </el-button>
                <el-button v-if="group.history.length" text @click="toggleGroup(group.format)">
                  {{ isExpanded(group.format) ? '收起' : `历史版本（${group.history.length}）` }}
                  <el-icon class="disclosure" :class="{ 'is-open': isExpanded(group.format) }">
                    <ArrowDown />
                  </el-icon>
                </el-button>
              </div>
            </div>

            <ul v-if="isExpanded(group.format)" class="history-list">
              <li v-for="item in group.history" :key="item.export_id" class="history-row">
                <span class="history-time">{{ formatDate(item.created_at) }}</span>
                <span v-if="versionLabel(item.artifact_version_id)" class="version-chip">
                  {{ versionLabel(item.artifact_version_id) }}
                </span>
                <span class="status-pill" :class="statusClass(item.status)">
                  {{ statusLabel(item.status) }}
                </span>
                <span class="history-file" :title="item.file_name || ''">
                  {{ item.file_name || '（无文件）' }}
                </span>
                <span class="history-meta">
                  {{
                    item.size_bytes
                      ? `${Math.ceil(item.size_bytes / 1024)} KB`
                      : item.error || '未完成'
                  }}
                </span>
                <span class="history-actions">
                  <el-button v-if="item.status === 'completed'" text @click="previewExport(item)">
                    预览
                  </el-button>
                  <el-button v-if="item.status === 'completed'" text @click="download(item)">
                    下载
                  </el-button>
                  <el-button v-else-if="item.status === 'failed'" text @click="retryExport(item)">
                    重试
                  </el-button>
                </span>
              </li>
            </ul>
          </li>
        </ul>
      </section>
    </template>

    <!-- HTML / PDF 直接渲染导出的真文件；PPT / 教案按蓝图数据渲染内容预览 -->
    <el-dialog
      v-model="previewOpen"
      class="preview-dialog"
      :title="previewName"
      width="min(1040px, 94vw)"
      top="5dvh"
      @closed="closePreview"
    >
      <!-- 互动内容要跑脚本，所以单独关进沙箱；PDF 不能带 sandbox：
           沙箱（哪怕是空的）会连浏览器内置的 PDF 阅读器一起禁用，画面只剩一个禁止图标。 -->
      <iframe
        v-if="previewKind === 'html' && previewUrl"
        class="preview-frame"
        :src="previewUrl"
        title="互动内容预览"
        sandbox="allow-scripts"
      />
      <iframe
        v-else-if="previewKind === 'pdf' && previewUrl"
        class="preview-frame"
        :src="previewUrl"
        title="打印版预览"
      />

      <div v-else-if="previewKind === 'slides'" class="deck-preview">
        <template v-if="previewSlide">
          <div class="deck-toolbar">
            <!-- 翻页是这一屏的主操作，所以给它实体按钮的份量：
                 描边 + 品牌色 + 图标 + 键盘 ←/→ 也能翻 -->
            <div class="deck-nav">
              <button type="button" class="deck-step" @click="stepPreviewSlide(-1)">
                <el-icon><ArrowLeft /></el-icon>
                上一页
              </button>
              <span class="deck-position">{{ previewSlideIndex + 1 }} / {{ previewSlides.length }}</span>
              <button type="button" class="deck-step" @click="stepPreviewSlide(1)">
                下一页
                <el-icon><ArrowRight /></el-icon>
              </button>
            </div>
            <span class="deck-hint">
              内容预览 · 版面与导出的 PPTX 同源，逐像素一致请用 PowerPoint 打开（← → 也可翻页）
            </span>
          </div>

          <!-- 页面条：整册有多少页一眼看得见，也可直接跳到任意一页。
               画布只渲染当前这一页，是为了不让一册 12 页一起画拖慢弹窗。 -->
          <ol class="deck-strip">
            <li v-for="(slide, index) in previewSlides" :key="slide.slide_id">
              <button
                type="button"
                class="deck-chip"
                :class="{ 'is-current': index === previewSlideIndex }"
                :title="slide.title"
                @click="previewSlideIndex = index"
              >
                <span class="deck-chip-order">{{ slide.order }}</span>
                <span class="deck-chip-title">{{ slide.title || '（未命名页面）' }}</span>
              </button>
            </li>
          </ol>

          <!-- 讲稿由 SlidePreview 自己渲染（画布下方的讲稿条），这里不要再画一遍 -->
          <SlidePreview :slide="previewSlide" :image-url="previewSlideImageUrl" />
        </template>
        <el-empty v-else description="这一版没有幻灯片内容" :image-size="90" />
      </div>

      <div v-else class="doc-preview">
        <template v-if="previewSnapshot">
          <h3 class="doc-title">{{ previewSnapshot.title }}</h3>
          <p class="doc-meta">
            授课对象 {{ previewOverview.audience || '未记录' }} ·
            {{ previewOverview.duration || 0 }} 分钟
          </p>
          <p v-if="previewOverview.goal" class="doc-goal">{{ previewOverview.goal }}</p>

          <section v-for="section in previewSections" :key="section.section_id" class="doc-section">
            <h4>
              {{ section.order }}. {{ section.title }}
              <span class="doc-minutes">{{ section.duration_minutes }} 分钟</span>
            </h4>
            <p class="doc-objective">{{ section.objective }}</p>
            <div class="doc-grid">
              <div>
                <span class="doc-label">教师活动</span>
                <ul>
                  <li v-for="(action, index) in section.teacher_actions" :key="index">{{ action }}</li>
                </ul>
              </div>
              <div>
                <span class="doc-label">学生活动</span>
                <ul>
                  <li v-for="(action, index) in section.student_actions" :key="index">{{ action }}</li>
                </ul>
              </div>
            </div>
            <p v-if="section.assessment" class="doc-assessment">评价：{{ section.assessment }}</p>
          </section>

          <section v-if="previewDocSpecs.homework" class="doc-section">
            <h4>课后任务</h4>
            <p class="doc-objective">{{ previewDocSpecs.homework }}</p>
          </section>
          <section v-if="(previewDocSpecs.differentiation || []).length" class="doc-section">
            <h4>分层任务</h4>
            <ul>
              <li v-for="(item, index) in previewDocSpecs.differentiation" :key="index">{{ item }}</li>
            </ul>
          </section>
          <section v-if="(previewDocSpecs.teacher_preparation || []).length" class="doc-section">
            <h4>课前准备</h4>
            <ul>
              <li v-for="(item, index) in previewDocSpecs.teacher_preparation" :key="index">{{ item }}</li>
            </ul>
          </section>
          <section v-if="(previewDocSpecs.reflection_prompts || []).length" class="doc-section">
            <h4>教学反思</h4>
            <ul>
              <li v-for="(item, index) in previewDocSpecs.reflection_prompts" :key="index">{{ item }}</li>
            </ul>
          </section>
          <p class="doc-hint">内容预览 · 数据与导出的 DOCX 一致，正式排版以 Word 打开为准</p>
        </template>
        <el-empty v-else description="找不到该版本的内容" :image-size="90" />
      </div>

      <template #footer>
        <el-button @click="previewOpen = false">关闭</el-button>
        <el-button v-if="previewItem" type="primary" @click="download(previewItem)">下载</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  Document,
  Download,
  MagicStick,
  Monitor,
  Printer,
  Refresh,
  View,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  createVersionExports,
  downloadExport,
  downloadMaterial,
  fetchArtifactVersions,
  fetchProjectExports,
  getApiErrorMessage,
} from '@/api'
import StageProgress from '@/components/progress/StageProgress.vue'
import SlidePreview from '@/components/preview/SlidePreview.vue'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import { SSEClient } from '@/utils/sse'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => projectStore.activeProjectId)
const versions = ref([])
const selectedVersionId = ref(String(route.query.artifactVersionId || ''))
const exports = ref([])
const loading = ref(false)
const exporting = ref(false)
const errorMessage = ref('')
let progressStream = null
let reconnectTimer = null
let streamClosed = false

const selectedVersion = computed(() => versions.value.find((version) => version.artifact_version_id === selectedVersionId.value))
/** 展开了历史版本的格式（默认全部收起，列表才清爽） */
const expandedFormats = ref(new Set())

const FORMAT_ICONS = { pptx: Monitor, docx: Document, pdf: Printer, html: MagicStick }
const FORMAT_ORDER = ['pptx', 'docx', 'pdf', 'html']

/**
 * 同一格式只留一行：最新一条作为主体，更早的导出收进 history。
 *
 * 重复导出会不断追加记录，平铺出来会让「导出记录」变成流水账；
 * 教师真正要的通常是"最新那份"，历史版本按需展开即可。
 */
const groupedExports = computed(() => {
  const groups = new Map()
  for (const item of exports.value) {
    const list = groups.get(item.format) || []
    list.push(item)
    groups.set(item.format, list)
  }
  return [...groups.entries()]
    .map(([format, list]) => {
      const sorted = [...list].sort(
        (left, right) => new Date(right.created_at) - new Date(left.created_at),
      )
      return { format, latest: sorted[0], history: sorted.slice(1) }
    })
    // 固定顺序：PPT → 教案 → 打印版 → 互动，避免随导出顺序跳动
    .sort((left, right) => FORMAT_ORDER.indexOf(left.format) - FORMAT_ORDER.indexOf(right.format))
})

function isExpanded(format) {
  return expandedFormats.value.has(format)
}

function toggleGroup(format) {
  const next = new Set(expandedFormats.value)
  if (next.has(format)) next.delete(format)
  else next.add(format)
  expandedFormats.value = next
}

function isRunning(item) {
  return item.status === 'pending' || item.status === 'processing'
}

function formatDate(value) {
  if (!value) return '时间未知'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

/** 导出记录绑在不可变的成果版本上，标出版本号才分得清哪份是哪份 */
function versionLabel(artifactId) {
  const version = versions.value.find((item) => item.artifact_version_id === artifactId)
  return version ? `v${version.version}` : ''
}

function metaText(item) {
  if (item.size_bytes) {
    return `${Math.ceil(item.size_bytes / 1024)} KB · ${formatDate(item.created_at)} · 可直接下载`
  }
  return item.error || '等待任务完成'
}

function formatIcon(format) {
  return FORMAT_ICONS[format] || Document
}

function statusLabel(status) {
  return { pending: '排队中', processing: '导出中', completed: '已完成', failed: '失败' }[status] || '未知'
}

// 颜色 + 文案一起表意，不单靠颜色
function statusClass(status) {
  return { completed: 'done', failed: 'failed', processing: 'running', pending: 'pending' }[status] || 'pending'
}

function formatLabel(format) {
  return { pptx: 'PPTX 演示文稿', docx: 'DOCX 教案', pdf: 'PDF 打印版', html: 'HTML5 互动内容' }[format] || format.toUpperCase()
}

async function loadVersions() {
  if (!projectId.value) return
  versions.value = (await fetchArtifactVersions(projectId.value)).data || []
  if (!selectedVersionId.value || !versions.value.some((version) => version.artifact_version_id === selectedVersionId.value)) {
    selectedVersionId.value = versions.value[0]?.artifact_version_id || ''
  }
}

async function loadExports() {
  if (!projectId.value || !selectedVersionId.value) {
    exports.value = []
    return
  }
  exports.value = (await fetchProjectExports(projectId.value, selectedVersionId.value)).data || []
}

/**
 * 订阅项目级进度流，替代原先 1.2 秒一次的轮询。
 * 服务端只在确实有状态变化时推送，空闲项目不再产生任何请求。
 */
function startProgressStream() {
  if (!projectId.value) return
  stopProgressStream()
  progressStream = new SSEClient(
    `/api/v1/projects/${projectId.value}/events`,
    {
      onProgress: async () => {
        try {
          await loadExports()
        } catch (error) {
          errorMessage.value = error.response?.data?.error?.message || '导出状态刷新失败，请手动刷新'
        }
      },
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
}

function scheduleReconnect() {
  if (streamClosed || !projectId.value || reconnectTimer) return
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    startProgressStream()
  }, 3000)
}

async function loadWorkspace() {
  loading.value = true
  errorMessage.value = ''
  try {
    await loadVersions()
    await loadExports()
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '版本或导出记录加载失败，请重试'
  } finally {
    loading.value = false
  }
}

async function createExports(force = false) {
  if (!projectId.value || !selectedVersionId.value || exporting.value) return
  exporting.value = true
  errorMessage.value = ''
  try {
    exports.value = (await createVersionExports(projectId.value, selectedVersionId.value, undefined, force)).data.exports || []
    ElMessage.success(force ? '已重新创建导出任务' : '已创建导出任务')
    // 进度由项目级 SSE 推送：新建的导出记录本身就会改变快照并触发刷新。
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '导出任务创建失败，请重试'
  } finally {
    exporting.value = false
  }
}

async function retryExport(item) {
  if (exporting.value) return
  exporting.value = true
  errorMessage.value = ''
  try {
    // 用这条记录自己的版本重导：历史记录可能来自别的成果版本，
    // 用当前选中的版本会导错内容。
    await createVersionExports(
      projectId.value,
      item.artifact_version_id || selectedVersionId.value,
      [item.format],
      true,
    )
    await loadExports()
    ElMessage.success(`已重新创建 ${formatLabel(item.format)} 导出任务`)
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '导出任务重试失败')
  } finally {
    exporting.value = false
  }
}

/**
 * 预览导出的成果。
 *
 * 四种格式的预览方式不同，因为浏览器能渲染什么差别很大：
 *   - html：iframe 直接跑这份文件（教具只存在于导出文件里，不在应用内重做）
 *   - pdf ：浏览器自带 PDF 阅读器，直接渲染真文件
 *   - pptx / docx：浏览器不能渲染这两种格式，所以按同一份蓝图数据渲染"内容预览"
 *     —— 幻灯片用与 pptx 版面同源的组件，教案用文档式排版。它展示的是内容，
 *     不是逐像素的 Office 版面（那需要 Office 或转换服务）。
 *
 * 不用下载接口的 URL 直接做 iframe src —— 那个响应带 Content-Disposition: attachment，
 * 浏览器在 iframe 里会直接触发下载而不是渲染。
 */
const previewOpen = ref(false)
const previewUrl = ref('')
const previewName = ref('')
const previewItem = ref(null)
// 'html' | 'pdf' | 'slides' | 'document'
const previewKind = ref('html')
const previewSnapshot = ref(null)
const previewSlideIndex = ref(0)
// 配图要带鉴权取回，先下载成 blob URL 再交给画布
const previewImageUrls = ref({})

const previewSlides = computed(() => previewSnapshot.value?.slides || [])
const previewSlide = computed(() => previewSlides.value[previewSlideIndex.value] || null)
const previewSlideImageUrl = computed(() => {
  const materialId = previewSlide.value?.image?.material_id
  return materialId ? previewImageUrls.value[materialId] || '' : ''
})
const previewSections = computed(() => previewSnapshot.value?.lesson_sections || [])
const previewDocSpecs = computed(() => previewSnapshot.value?.output_specs?.docx || {})
const previewOverview = computed(() => ({
  audience: previewSnapshot.value?.target_audience || '',
  duration: previewSnapshot.value?.duration_minutes || 0,
  goal: previewSnapshot.value?.teaching_goal || '',
}))

async function previewExport(item) {
  try {
    previewItem.value = item
    previewName.value = item.file_name || `${formatLabel(item.format)}预览`
    if (item.format === 'html' || item.format === 'pdf') {
      const response = await downloadExport(item.export_id)
      releasePreviewUrls()
      previewUrl.value = window.URL.createObjectURL(
        new Blob([response.data], {
          type: item.format === 'pdf' ? 'application/pdf' : 'text/html',
        }),
      )
      previewKind.value = item.format === 'pdf' ? 'pdf' : 'html'
      previewOpen.value = true
      return
    }
    const version = versions.value.find(
      (candidate) => candidate.artifact_version_id === item.artifact_version_id,
    )
    previewSnapshot.value = version?.snapshot || null
    previewSlideIndex.value = 0
    previewKind.value = item.format === 'pptx' ? 'slides' : 'document'
    previewOpen.value = true
    if (item.format === 'pptx') await loadPreviewImages()
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '预览加载失败，请稍后重试')
  }
}

/** 幻灯片里的配图存在素材库，取回需要鉴权，所以下载成 blob URL 再给画布用 */
async function loadPreviewImages() {
  const materialIds = [
    ...new Set(
      previewSlides.value
        .map((slide) => slide.image?.material_id)
        .filter((materialId) => materialId && !previewImageUrls.value[materialId]),
    ),
  ]
  await Promise.all(
    materialIds.map(async (materialId) => {
      try {
        const response = await downloadMaterial(materialId)
        previewImageUrls.value = {
          ...previewImageUrls.value,
          [materialId]: window.URL.createObjectURL(response.data),
        }
      } catch {
        // 图片取不到就不显示，不影响预览本身
      }
    }),
  )
}

function stepPreviewSlide(offset) {
  const total = previewSlides.value.length
  if (!total) return
  previewSlideIndex.value = (previewSlideIndex.value + offset + total) % total
}

/** 幻灯片预览开着时，← → 也能翻页 */
function handlePreviewKeydown(event) {
  if (previewKind.value !== 'slides') return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    stepPreviewSlide(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    stepPreviewSlide(1)
  }
}

watch(previewOpen, (open) => {
  if (open) window.addEventListener('keydown', handlePreviewKeydown)
  else window.removeEventListener('keydown', handlePreviewKeydown)
})

function releasePreviewUrls() {
  if (previewUrl.value) window.URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  for (const url of Object.values(previewImageUrls.value)) window.URL.revokeObjectURL(url)
  previewImageUrls.value = {}
}

function closePreview() {
  releasePreviewUrls()
  previewSnapshot.value = null
}

async function download(item) {
  try {
    const response = await downloadExport(item.export_id)
    const url = window.URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = item.file_name || `${item.format}-export`
    link.click()
    window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '下载失败，请稍后重试')
  }
}

function openEditor() {
  router.push({ path: '/editor', query: { artifactVersionId: selectedVersionId.value } })
}

onMounted(async () => {
  await loadWorkspace()
  startProgressStream()
})
onBeforeUnmount(() => {
  streamClosed = true
  stopProgressStream()
  window.removeEventListener('keydown', handlePreviewKeydown)
})
</script>

<style scoped>
/* ── 导出设置 ───────────────────────────────────────────── */
.export-controls {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-5);
  flex-wrap: wrap;
}

.control-field {
  min-width: 0;
  flex: 1 1 320px;
}

.field-label {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.version-select { width: 100%; }

.export-actions {
  display: flex;
  gap: var(--space-2);
  flex: 0 0 auto;
}

.hint {
  margin-top: var(--space-4);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.list-hint {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

/* ── 导出记录 ───────────────────────────────────────────── */
.export-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 分隔放在"格式组"上：一个格式一行，历史版本展开后仍属于同一组 */
.export-group { border-bottom: 1px solid var(--border-hairline); }

.export-group:last-child { border-bottom: 0; }

.export-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-4) var(--space-2);
  border-radius: var(--radius-md);
  transition: background-color var(--duration-fast) var(--ease-standard);
}

.export-row:hover { background: var(--neutral-50); }

.version-chip {
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--bg-surface-sunken);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.disclosure {
  margin-left: var(--space-1);
  transition: transform var(--duration-fast) var(--ease-standard);
}

.disclosure.is-open { transform: rotate(180deg); }

/* ── 历史版本：默认收起，展开后是紧凑的一行一条 ─────────────── */
.history-list {
  display: flex;
  flex-direction: column;
  margin: 0 0 var(--space-3) 0;
  padding: var(--space-2) var(--space-3) var(--space-2) var(--space-10);
  list-style: none;
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
}

.history-row {
  display: grid;
  grid-template-columns: auto auto auto minmax(0, 1fr) auto auto;
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--border-hairline);
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

.history-row:last-child { border-bottom: 0; }

.history-time { font-variant-numeric: tabular-nums; }

.history-file {
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta { font-variant-numeric: tabular-nums; }

.history-actions { justify-self: end; }

/* ── 预览：iframe 高度按视口给，配合对话框自身的滚动 ───────── */
.preview-dialog :deep(.el-dialog__body) {
  padding: var(--space-2) var(--space-4) var(--space-4);
}

.preview-frame {
  width: 100%;
  height: 70dvh;
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
}

/* ── PPT / 教案的内容预览 ───────────────────────────────── */
.deck-preview {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-height: 74dvh;
  overflow-y: auto;
}

.deck-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.deck-nav {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* 与实心主按钮同一套对比度规则：描边与文字用 800（4.52:1），
   悬停填 900 配白字（6.31:1）。500/600 在白底上只有 2.2~2.7:1。 */
.deck-step {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  min-height: 34px;
  padding: var(--space-2) var(--space-4);
  border: 1px solid var(--brand-800);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  color: var(--text-brand);
  font-family: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.deck-step:hover {
  background: var(--brand-900);
  color: var(--text-inverse);
}

/* 焦点环由 base.css 统一提供（:focus-visible → --ring-brand），这里不再重复 */

.deck-position {
  min-width: 56px;
  text-align: center;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.deck-hint {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.deck-strip {
  display: flex;
  gap: var(--space-2);
  margin: 0;
  padding: 0 0 var(--space-2);
  list-style: none;
  overflow-x: auto;
}

.deck-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  max-width: 190px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  cursor: pointer;
  transition: border-color var(--duration-fast) var(--ease-standard),
    background-color var(--duration-fast) var(--ease-standard);
}

.deck-chip:hover { background: var(--neutral-50); }

.deck-chip.is-current {
  border-color: var(--border-brand);
  background: var(--brand-50);
  color: var(--text-primary);
}

.deck-chip-order {
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.deck-chip-title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.doc-preview {
  max-height: 74dvh;
  overflow-y: auto;
  padding-right: var(--space-2);
}

.doc-title {
  margin: 0 0 var(--space-2);
  color: var(--text-primary);
  font-size: var(--text-lg);
}

.doc-meta,
.doc-hint {
  margin: 0 0 var(--space-3);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.doc-goal {
  margin: 0 0 var(--space-4);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.doc-section {
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border-hairline);
}

.doc-section h4 {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin: 0 0 var(--space-2);
  color: var(--text-primary);
  font-size: var(--text-base);
}

.doc-minutes {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.doc-objective,
.doc-assessment {
  margin: 0 0 var(--space-2);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.doc-assessment { color: var(--text-tertiary); }

.doc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-3);
}

.doc-label {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.doc-grid ul,
.doc-section ul {
  margin: var(--space-1) 0 0;
  padding-left: var(--space-5);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

/* 格式色标：只用文件类型色，不参与主色体系 */
.format-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-lg);
  background: var(--bg-surface-sunken);
  font-size: var(--text-lg);
}

.format-mark.is-pptx { color: var(--file-ppt); }
.format-mark.is-docx { color: var(--file-word); }
.format-mark.is-pdf { color: var(--file-pdf); }
.format-mark.is-html { color: var(--text-brand); }

.export-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.export-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.export-heading strong {
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
}

.export-file,
.export-meta {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.export-file { color: var(--text-secondary); }

.export-progress { margin-top: var(--space-2); }

.row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  justify-content: flex-end;
}

@media (max-width: 768px) {
  .export-controls { align-items: stretch; }

  .export-actions > :deep(.el-button) { flex: 1; }

  .export-row {
    grid-template-columns: 32px minmax(0, 1fr);
    grid-template-areas:
      'mark main'
      'mark actions';
    align-items: start;
    gap: var(--space-2) var(--space-3);
  }

  .format-mark {
    grid-area: mark;
    width: 32px;
    height: 32px;
    border-radius: var(--radius-md);
    font-size: var(--text-base);
  }

  .export-main { grid-area: main; }

  .row-actions {
    grid-area: actions;
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .row-actions :deep(.el-button) { min-width: 36px; height: 36px; }

  /* 历史版本在窄屏改成两行：时间/版本/状态一行，文件名与操作一行 */
  .history-list { padding-left: var(--space-3); }

  .history-row {
    grid-template-columns: auto auto minmax(0, 1fr);
    grid-template-areas:
      'time chip status'
      'file file actions';
    row-gap: var(--space-1);
  }

  .history-time { grid-area: time; }
  .history-row .version-chip { grid-area: chip; }
  .history-row .status-pill { grid-area: status; }
  .history-file { grid-area: file; }
  .history-meta { display: none; }
  .history-actions { grid-area: actions; }
}
</style>
