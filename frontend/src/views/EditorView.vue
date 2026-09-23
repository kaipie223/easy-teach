<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>成果编辑</h1>
        <p>修改会创建新的成果版本，历史版本保持可恢复。</p>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="loadWorkspace">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button
          type="primary"
          :loading="generating"
          :disabled="!projectId || generating || taskActive"
          @click="startGenerationFlow"
        >
          <el-icon><MagicStick /></el-icon>
          生成课件
        </el-button>
        <!-- 加括号调用：裸绑定会把 MouseEvent 当作 version 传进来，
             导出页就拿不到 artifactVersionId，只能退回到最新版本。 -->
        <el-button :disabled="!currentVersion" @click="openExports()">
          <el-icon><Download /></el-icon>
          导出当前版本
        </el-button>
      </div>
    </header>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-skeleton v-if="loading && !task && !versions.length" :rows="7" animated />

    <template v-else>
      <section v-if="task" class="section-card">
        <div class="section-header">
          <div>
            <h2>最近生成任务</h2>
            <p>任务完成后，成果文件仍绑定到本次生成的具体版本。</p>
          </div>
          <el-tag :type="task.status === 'completed' ? 'success' : task.status === 'failed' ? 'danger' : 'info'">
            {{ statusLabel }}
          </el-tag>
        </div>
        <StageProgress
          class="task-progress"
          :percent="task.progress"
          :label="task.stage_label || statusLabel"
          :steps="task.stages"
          :current="task.stage"
          :started-at="task.started_at"
          :done="task.status === 'completed'"
        />
        <div class="task-summary">
          <div>
            <span class="muted">任务</span>
            <strong>{{ task.task_id }}</strong>
          </div>
          <div>
            <span class="muted">版本</span>
            <strong>{{ task.artifact_version_id || '兼容任务' }}</strong>
          </div>
          <div>
            <span class="muted">状态</span>
            <strong :class="statusClass">{{ statusLabel }}</strong>
          </div>
        </div>
        <div v-if="task.outputs?.length" class="output-grid">
          <article v-for="output in task.outputs" :key="output.file_id" class="output-item">
            <div class="output-icon">{{ output.file_type.toUpperCase() }}</div>
            <div class="output-content">
              <strong>{{ output.file_name }}</strong>
              <span>{{ output.size_kb }} KB · {{ outputDescription(output.file_type) }}</span>
            </div>
            <el-button type="primary" plain @click="downloadOutput(output)">
              <el-icon><Download /></el-icon>
              下载
            </el-button>
          </article>
        </div>
        <el-alert
          v-if="task.status === 'failed'"
          :title="task.error || '生成失败，版本数据仍然保留。'"
          type="error"
          show-icon
          :closable="false"
        />
      </section>

      <section v-if="versions.length" class="section-card preview-section">
        <div class="section-header">
          <div>
            <h2>成果预览</h2>
            <p>预览的是与导出 pptx 同源的内容。点击标题、要点或讲稿即可选中一处，再单独对它提修改意见。</p>
          </div>
          <el-select
            v-model="previewVersionId"
            class="preview-version"
            placeholder="选择版本"
            @change="onPreviewVersionChange"
          >
            <el-option
              v-for="version in versions"
              :key="version.artifact_version_id"
              :label="`v${version.version} · ${generationModeLabel(version.generation_mode)}`"
              :value="version.artifact_version_id"
            >
              <span>v{{ version.version }} · {{ generationModeLabel(version.generation_mode) }}</span>
              <span class="option-hint">{{ formatDate(version.created_at) }}</span>
            </el-option>
          </el-select>
        </div>

        <el-alert
          v-if="!canAnnotate"
          title="正在预览历史版本，只能查看。切到最新版本才能提修改意见。"
          type="warning"
          show-icon
          :closable="false"
        />

        <div class="preview-body">
          <div class="preview-rail">
            <button
              v-for="slide in previewSlides"
              :key="slide.slide_id"
              type="button"
              class="rail-item"
              :class="{ 'is-current': slide.slide_id === previewSlide?.slide_id }"
              @click="selectPreviewSlide(slide)"
            >
              <span class="rail-order">{{ slide.order }}</span>
              <span class="rail-title">{{ slide.title || '（未命名页面）' }}</span>
            </button>
          </div>

          <div class="preview-main">
            <SlidePreview
              v-if="previewSlide"
              :slide="previewSlide"
              :image-url="previewImageUrl"
              :active-field="previewAnchorField"
              :active-index="previewAnchorIndex"
              @select="selectPreviewElement"
            />
            <el-empty v-else description="这个版本没有幻灯片内容" :image-size="90" />
          </div>
        </div>

        <div class="preview-image">
          <!-- AI 生成配图：不必先去资料页上传图片，直接描述想要的画面即可 -->
          <el-input
            v-model="aiImagePrompt"
            maxlength="600"
            show-word-limit
            placeholder="描述想要的配图，例如：切开的西瓜放在木桌上，扁平化教学插画，无文字"
            :disabled="!canAnnotate || generatingImage"
            @keydown.enter.exact.prevent="generateSlideImageByAI"
          >
            <template #append>
              <el-button
                :loading="generatingImage"
                :disabled="!canGenerateImage"
                @click="generateSlideImageByAI"
              >
                <el-icon v-if="!generatingImage"><MagicStick /></el-icon>
                AI 生成配图
              </el-button>
            </template>
          </el-input>

          <div class="preview-image-row">
            <div class="preview-image-pick">
              <el-select
                v-model="imageMaterialId"
                placeholder="选择一张图片资料"
                filterable
                clearable
                :disabled="!canAnnotate || savingImage"
              >
                <el-option
                  v-for="item in imageMaterials"
                  :key="item.material_id"
                  :label="item.original_name"
                  :value="item.material_id"
                />
              </el-select>
              <img v-if="selectedImageUrl" class="preview-image-thumb" :src="selectedImageUrl" alt="" />
            </div>
            <el-segmented
              v-model="imagePlacement"
              :options="PLACEMENT_OPTIONS"
              :disabled="!canAnnotate || savingImage || !imageMaterialId"
              @change="changeSlidePlacement"
            />
          </div>
          <el-input
            v-model="imageCaption"
            maxlength="200"
            show-word-limit
            placeholder="图注（可选，显示在图片下方；背景图模式下并入讲稿）"
            :disabled="!canAnnotate || savingImage || !imageMaterialId"
          />
          <p v-if="imageDirty" class="preview-image-hint">
            素材或图注有改动，点下面的按钮生效；位置切换会立即生效。
          </p>
          <div class="revision-actions">
            <el-button type="primary" :loading="savingImage" :disabled="!canApplyImage" @click="applySlideImage">
              <el-icon><Picture /></el-icon>
              应用配图并创建版本
            </el-button>
            <el-button v-if="previewSlide?.image" :disabled="savingImage" @click="removeSlideImage">
              移除配图
            </el-button>
            <!-- 一键补齐其余缺图页：整批只产生一个新版本 -->
            <el-button :loading="illustrating" :disabled="!canIllustrate" @click="illustrateMissingSlides">
              <el-icon><MagicStick /></el-icon>
              为缺图页自动配图{{ missingImageCount ? `（${missingImageCount} 页）` : '' }}
            </el-button>
          </div>
          <p v-if="!imageMaterials.length" class="preview-image-hint">
            本项目还没有图片资料：可以直接在上面用 AI 生成配图，或到「资料」页上传一张图片。
          </p>
        </div>

        <div class="preview-instruction">
          <label class="field-label" for="slide-anchor-instruction">
            对「{{ anchorDescription }}」提意见
          </label>
          <el-input
            id="slide-anchor-instruction"
            v-model="aiInstruction"
            type="textarea"
            :rows="2"
            maxlength="2000"
            show-word-limit
            placeholder="例如：这条要点太长了，改成学生一眼能看懂的短句"
            :disabled="!canAnnotate || regenerating"
          />
          <div class="revision-actions">
            <el-button type="primary" :loading="regenerating" :disabled="!canRegenerate" @click="regenerateTarget">
              <el-icon><MagicStick /></el-icon>
              按意见重生成并创建版本
            </el-button>
            <el-button v-if="aiField" :disabled="regenerating" @click="clearElementAnchor">取消选中</el-button>
          </div>
          <StageProgress
            v-if="regenerating"
            :percent="regenPercent"
            :label="regenStageLabel"
            :steps="regenStages"
            :current="regenStageKey"
            :started-at="regenStartedAt"
          />
        </div>
      </section>

      <section v-if="projectId && versions.length" class="section-card outputs-section">
        <div class="section-header">
          <div>
            <h2>生成产物</h2>
            <p>生成任务写出的文件会绑定到它当时使用的版本。重新生成不改变版本内容，只替换这些文件。</p>
          </div>
          <el-tag v-if="previewVersion" effect="plain">v{{ previewVersion.version }}</el-tag>
        </div>
        <div v-if="versionFiles.length" class="output-grid">
          <article v-for="file in versionFiles" :key="file.file_id" class="output-item">
            <div class="output-icon">{{ (file.file_type || '').toUpperCase() }}</div>
            <div class="output-content">
              <strong>{{ outputName(file) }}</strong>
              <span>{{ file.size_kb }} KB · {{ outputDescription(file.file_type) }}</span>
            </div>
            <el-button type="primary" plain @click="downloadOutput(file)">
              <el-icon><Download /></el-icon>
              下载
            </el-button>
          </article>
        </div>
        <p v-else class="outputs-empty">
          这个版本还没有生成产物。点右上角「生成课件」产出 pptx / docx / html，或到「导出」页生成四种格式。
        </p>
      </section>

      <section v-if="projectId" class="section-card revision-section">
        <div class="section-header">
          <div>
            <h2>局部修改</h2>
            <p>可以让 AI 只重写一个目标，也可以使用确定性的结构化修改；两种方式都会创建新版本。</p>
          </div>
          <el-tag v-if="currentVersion" effect="plain">基于 v{{ currentVersion.version }}</el-tag>
        </div>
        <el-tabs v-model="revisionMode">
          <el-tab-pane label="AI 局部重生成" name="ai">
            <div class="ai-revision-form">
              <div class="target-selectors">
                <el-segmented v-model="aiTargetType" :options="targetTypeOptions" />
                <el-select v-model="aiTargetId" placeholder="选择修改目标" filterable>
                  <el-option v-for="item in targetOptions" :key="item.value" :label="item.label" :value="item.value" />
                </el-select>
              </div>
              <label class="field-label" for="ai-revision-instruction">重生成要求</label>
              <el-input
                id="ai-revision-instruction"
                v-model="aiInstruction"
                type="textarea"
                :rows="3"
                maxlength="2000"
                show-word-limit
                placeholder="例如：改成更适合初二学生的实验探究表达，补充具体追问和易错点"
                :disabled="!currentVersion || regenerating"
              />
              <div class="revision-actions">
                <el-button type="primary" :loading="regenerating" :disabled="!canRegenerate" @click="regenerateTarget">
                  <el-icon><MagicStick /></el-icon>
                  AI 重生成并创建版本
                </el-button>
              </div>
              <StageProgress
            v-if="regenerating"
            :percent="regenPercent"
            :label="regenStageLabel"
            :steps="regenStages"
            :current="regenStageKey"
            :started-at="regenStartedAt"
          />
            </div>
          </el-tab-pane>
          <el-tab-pane label="结构化修改" name="manual">
            <div class="manual-revision-form">
              <label class="field-label" for="revision-instruction">修改意见</label>
              <el-input
                id="revision-instruction"
                v-model="revisionInstruction"
                type="textarea"
                :rows="3"
                maxlength="2000"
                show-word-limit
                placeholder="例如：简化第 3 页"
                :disabled="!currentVersion || submitting"
              />
              <div class="revision-actions">
                <el-button type="primary" :loading="interpreting" :disabled="!currentVersion || !revisionInstruction.trim() || submitting" @click="previewRevision">预览修改</el-button>
                <el-button v-if="patch" :loading="submitting" @click="applyCurrentPatch">应用并创建新版本</el-button>
              </div>
              <el-alert v-if="patch" class="patch-preview" type="info" show-icon :closable="false">
                <template #title>{{ patch.summary }}</template>
                <div>目标：{{ patch.target_ids.join('、') || '未指定' }}</div>
                <div v-if="patch.cascade_check.length">需要同步检查：{{ patch.cascade_check.join('、') }}</div>
                <div v-if="patch.requires_confirmation">该修改影响范围较大，应用时会再次确认。</div>
              </el-alert>
            </div>
          </el-tab-pane>
        </el-tabs>
      </section>

      <section v-if="versions.length" class="section-card">
        <div class="section-header">
          <div>
            <h2>版本历史</h2>
            <p>恢复会复制旧快照并创建新版本，不会删除中间版本。</p>
          </div>
        </div>
        <div class="version-list">
          <article v-for="version in versions" :key="version.artifact_version_id" class="version-row">
            <div class="version-main">
              <div class="version-heading">
                <strong>v{{ version.version }}</strong>
                <el-tag size="small" effect="plain">{{ generationModeLabel(version.generation_mode) }}</el-tag>
                <el-tag v-if="version.artifact_version_id === currentVersion?.artifact_version_id" type="success" size="small">
                  当前版本
                </el-tag>
              </div>
              <span>{{ version.summary }}</span>
              <small>{{ formatDate(version.created_at) }} · 来源蓝图 {{ version.source_plan_id }}</small>
              <small v-if="version.model_name">{{ version.model_name }} · {{ version.prompt_version }}</small>
            </div>
            <div class="version-actions">
              <el-button plain @click="exportVersion(version)">导出此版本</el-button>
              <el-button
                v-if="version.artifact_version_id !== currentVersion?.artifact_version_id"
                type="warning"
                plain
                @click="restore(version)"
              >
                恢复此版本
              </el-button>
            </div>
          </article>
        </div>
      </section>

      <el-empty v-if="!projectId" description="缺少项目 ID，请从项目工作流进入成果编辑" />
      <el-empty v-else-if="!versions.length && !loading" description="尚未生成成果版本，请先完成蓝图生成" />
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Download, MagicStick, Picture, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  applyRevision,
  downloadFile,
  downloadMaterial,
  fetchArtifactVersions,
  fetchProjectMaterials,
  fetchVersionFiles,
  generateSlideImage,
  getApiErrorMessage,
  illustrateVersion,
  interpretRevision,
  restoreArtifactVersion,
  setSlideImage,
  startProjectGeneration,
} from '@/api'
import StageProgress from '@/components/progress/StageProgress.vue'
import SlidePreview from '@/components/preview/SlidePreview.vue'
import { SSEClient } from '@/utils/sse'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => projectStore.activeProjectId)
const task = ref(null)
const versions = ref([])
const patch = ref(null)
const revisionInstruction = ref('')
const loading = ref(false)
const interpreting = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const revisionMode = ref('ai')
const aiTargetType = ref('slide')
const aiTargetId = ref('')
const aiInstruction = ref('')
const regenerating = ref(false)
const regenStageLabel = ref('')
const regenPercent = ref(0)
const regenStageKey = ref('')
const regenStages = ref([])
// 已用时长纯前端算，后端只需要给开始时刻
const regenStartedAt = ref(null)
const generating = ref(false)
// 只有"用户手动选中了非最新版本"才固定预览；否则预览始终跟随最新版本，
// 这样生成与局部修改产生的新版本都能立刻看到。
const previewPinned = ref(false)
const previewVersionId = ref('')
// 当前预览版本已生成的产物。任务 ID 是一次性的（刷新即丢），文件记录才是持久的，
// 所以按版本查文件而不是按任务查。
const versionFiles = ref([])
const previewSlideId = ref('')
// 配图要带鉴权才能取，先下载成 blob URL 再交给画布
const slideImageUrls = ref({})
const pendingSlideImages = new Set()
// 配图表单：候选是本项目上传的图片资料
const imageMaterials = ref([])
const imageMaterialId = ref('')
const imagePlacement = ref('right')
const imageCaption = ref('')
const savingImage = ref(false)
// AI 生成配图：一句提示词即可，生成后按普通图片资料落库再绑定到当前页
const aiImagePrompt = ref('')
const generatingImage = ref(false)
// 批量给缺图页配图：整批只创建一个新版本
const illustrating = ref(false)
// 元素级锚点：先定位到某一页的某个字段，列表字段再细分到某一条
const aiField = ref(null)
const aiIndex = ref(null)
let taskStream = null

const currentVersion = computed(() => versions.value[0] || null)
const statusLabel = computed(() => ({
  pending: '排队中',
  processing: '生成中',
  completed: '已完成',
  failed: '失败',
}[task.value?.status] || '未知'))
const statusClass = computed(() => task.value?.status === 'completed' ? 'success-text' : 'muted')
const taskActive = computed(() => ['pending', 'processing'].includes(task.value?.status))
const targetTypeOptions = [
  { label: 'PPT 页面', value: 'slide' },
  { label: '教案章节', value: 'lesson_section' },
  { label: '互动题', value: 'interaction' },
]
const targetOptions = computed(() => {
  const snapshot = currentVersion.value?.snapshot
  if (!snapshot) return []
  if (aiTargetType.value === 'slide') return (snapshot.slides || []).map(item => ({ value: item.slide_id, label: `第 ${item.order} 页 · ${item.title}` }))
  if (aiTargetType.value === 'lesson_section') return (snapshot.lesson_sections || []).map(item => ({ value: item.section_id, label: `${item.order}. ${item.title}` }))
  return (snapshot.interactions || []).map(item => ({ value: item.interaction_id, label: item.title }))
})
const FIELD_LABELS = { title: '标题', purpose: '教学目的', bullets: '要点', speaker_notes: '讲稿' }
const previewVersion = computed(() => (
  versions.value.find(item => item.artifact_version_id === previewVersionId.value) || currentVersion.value
))
const previewSlides = computed(() => previewVersion.value?.snapshot?.slides || [])
const previewSlide = computed(() => (
  previewSlides.value.find(slide => slide.slide_id === previewSlideId.value) || previewSlides.value[0] || null
))
/** 只有预览最新版本时才能提意见：后端要求重生成的基准就是最新版本。 */
const canAnnotate = computed(() => Boolean(
  previewVersion.value
  && currentVersion.value
  && previewVersion.value.artifact_version_id === currentVersion.value.artifact_version_id,
))
const previewImageUrl = computed(() => {
  const materialId = previewSlide.value?.image?.material_id
  return materialId ? (slideImageUrls.value[materialId] || '') : ''
})
/** 锚点只在预览的正是被选中那一页时才高亮。 */
const previewAnchorField = computed(() => (
  aiTargetType.value === 'slide' && aiTargetId.value === previewSlide.value?.slide_id ? (aiField.value || '') : ''
))
const previewAnchorIndex = computed(() => (previewAnchorField.value ? aiIndex.value : null))
const anchorDescription = computed(() => {
  if (!aiTargetId.value) return '未选中目标'
  const base = targetOptions.value.find(item => item.value === aiTargetId.value)?.label || aiTargetId.value
  if (!aiField.value) return `${base}（整页）`
  const label = FIELD_LABELS[aiField.value] || aiField.value
  return aiIndex.value === null ? `${base} · ${label}` : `${base} · ${label}第 ${aiIndex.value + 1} 条`
})
const canRegenerate = computed(() => Boolean(
  canAnnotate.value && aiTargetId.value && aiInstruction.value.trim() && !regenerating.value,
))
const PLACEMENT_OPTIONS = [
  { label: '右侧图文', value: 'right' },
  { label: '整页大图', value: 'full' },
  { label: '背景图', value: 'background' },
]
const canApplyImage = computed(() => Boolean(
  canAnnotate.value && previewSlide.value && imageMaterialId.value && !savingImage.value,
))
/** 还没有配图的页面数：批量配图按钮要告诉教师这一下会生成几张图。 */
const missingImageCount = computed(() => (
  previewSlides.value.filter(slide => !slide?.image?.material_id).length
))
const canIllustrate = computed(() => Boolean(
  canAnnotate.value
  && previewSlide.value
  && missingImageCount.value
  && !illustrating.value
  && !savingImage.value,
))
/** 表单与已保存的配图状态不一致时提示"要点按钮才生效"，避免以为操作没反应。 */
const imageDirty = computed(() => {
  if (!previewSlide.value) return false
  const saved = previewSlide.value.image || null
  return (
    (imageMaterialId.value || '') !== (saved?.material_id || '')
    || imageCaption.value.trim() !== String(saved?.caption || '').trim()
  )
})
const canGenerateImage = computed(() => Boolean(
  canAnnotate.value
  && previewSlide.value
  && aiImagePrompt.value.trim().length >= 2
  && !generatingImage.value,
))
/** 选中素材的缩略图，用来确认选对了哪一张 */
const selectedImageUrl = computed(() => slideImageUrls.value[imageMaterialId.value] || '')

function outputDescription(type) {
  return { pptx: '演示文稿', docx: '教学教案', pdf: '打印版', html: '互动练习' }[type] || '生成文件'
}

function generationModeLabel(mode) {
  return { initial: '初始版本', manual: '人工修改', ai: 'AI 重生成', restore: '恢复版本' }[mode] || '人工修改'
}

function formatDate(value) {
  if (!value) return '时间未知'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadVersions() {
  if (!projectId.value) return
  const response = await fetchArtifactVersions(projectId.value)
  versions.value = response.data || []
}

function loadTask() {
  const taskId = String(route.query.taskId || '')
  taskStream?.disconnect()
  taskStream = null
  if (!taskId) {
    task.value = null
    return
  }
  watchTask(taskId)
}

/**
 * 通过 SSE 订阅任务进度，替代原先每秒一次的轮询。
 * 服务端只在快照变化时推送，任务结束时下发 result 帧并自动关闭连接。
 */
function watchTask(taskId) {
  taskStream = new SSEClient(
    `/api/v1/tasks/${taskId}/events`,
    {
      onProgress: (payload) => { task.value = payload },
      onResult: async (payload) => {
        task.value = payload
        await loadVersions()
        // 生成写出新产物，结束时立刻同步，避免"生成了但页面没变化"
        await loadVersionFiles()
      },
      onServiceError: (details) => {
        errorMessage.value = details?.message || '生成状态获取失败，版本数据仍可刷新查看'
      },
      onError: () => {
        errorMessage.value = '生成状态连接中断，版本数据仍可刷新查看'
      },
    },
    // 该端点只提供 GET，用默认的 POST 会 405
    { method: 'GET' },
  )
  taskStream.connect()
}

async function loadWorkspace() {
  loading.value = true
  errorMessage.value = ''
  try {
    await Promise.all([loadTask(), loadVersions()])
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '成果版本加载失败，请重试'
  } finally {
    loading.value = false
  }
}

async function previewRevision() {
  if (!projectId.value || !currentVersion.value || !revisionInstruction.value.trim()) return
  interpreting.value = true
  errorMessage.value = ''
  try {
    patch.value = (await interpretRevision(
      projectId.value,
      revisionInstruction.value.trim(),
      currentVersion.value.artifact_version_id,
    )).data
  } catch (error) {
    patch.value = null
    errorMessage.value = error.response?.data?.error?.message || '无法确定修改目标，请补充页面和修改内容'
  } finally {
    interpreting.value = false
  }
}

async function applyCurrentPatch() {
  if (!patch.value || submitting.value) return
  if (patch.value.requires_confirmation) {
    try {
      await ElMessageBox.confirm(
        `将基于 v${currentVersion.value.version} 创建新版本，影响 ${patch.value.target_ids.join('、')}。`,
        '确认应用修改',
        { confirmButtonText: '创建新版本', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const created = (await applyRevision(projectId.value, patch.value.patch_id, true)).data
    patch.value = null
    revisionInstruction.value = ''
    await loadVersions()
    ElMessage.success(`已创建成果版本 v${created.version}`)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '修改未应用，原版本仍然保留'
  } finally {
    submitting.value = false
  }
}

/** 把 SSE 错误包装成与 axios 同构的形状，上层 catch 逻辑无需改动。 */
function streamFailure(code, message) {
  return { response: { data: { error: { code, message } } } }
}

/** 切换预览页面：重生成目标同步切到这一页，并清掉上一页的元素锚点。 */
function selectPreviewSlide(slide) {
  previewSlideId.value = slide.slide_id
  aiTargetType.value = 'slide'
  aiTargetId.value = slide.slide_id
  clearElementAnchor()
}

/** 点击画布里的标题/要点/讲稿：把点击位置换算成元素级锚点。 */
function selectPreviewElement({ field, index }) {
  if (!previewSlide.value) return
  aiTargetType.value = 'slide'
  aiTargetId.value = previewSlide.value.slide_id
  aiField.value = field
  aiIndex.value = index ?? null
}

function clearElementAnchor() {
  aiField.value = null
  aiIndex.value = null
}

/**
 * 配图存在素材库里，取回需要鉴权，所以下载成 blob URL 再给画布用。
 * 取不到就留空：导出侧遇到同样情况也会跳过配图，不会因此失败。
 */
async function ensureImage(materialId) {
  if (!materialId || slideImageUrls.value[materialId] || pendingSlideImages.has(materialId)) return
  pendingSlideImages.add(materialId)
  try {
    const response = await downloadMaterial(materialId)
    slideImageUrls.value = {
      ...slideImageUrls.value,
      [materialId]: window.URL.createObjectURL(response.data),
    }
  } catch {
    // 图片不可用时不打扰用户，画布上不显示配图即可
  } finally {
    pendingSlideImages.delete(materialId)
  }
}

async function ensureSlideImages(slides) {
  const materialIds = new Set(slides.map(slide => slide.image?.material_id).filter(Boolean))
  for (const materialId of materialIds) {
    await ensureImage(materialId)
  }
}

async function loadImageMaterials() {
  if (!projectId.value) {
    imageMaterials.value = []
    return
  }
  try {
    const response = await fetchProjectMaterials(projectId.value)
    imageMaterials.value = (response.data || []).filter(
      item => item.file_type === 'image' && item.status !== 'archived',
    )
  } catch {
    // 候选列表取不到不影响预览，其它修改入口照常可用
    imageMaterials.value = []
  }
}

/** 把当前页的配图状态同步到表单，切页或切版本后不残留上一页的设置。 */
function syncImageForm(slide) {
  const image = slide?.image || null
  imageMaterialId.value = image?.material_id || ''
  imagePlacement.value = image?.placement || 'right'
  imageCaption.value = image?.caption || ''
}

async function saveSlideImage(materialId) {
  const keptSlideId = previewSlide.value?.slide_id
  savingImage.value = true
  errorMessage.value = ''
  try {
    await setSlideImage(
      projectId.value,
      currentVersion.value.artifact_version_id,
      keptSlideId,
      {
        material_id: materialId,
        placement: imagePlacement.value,
        caption: imageCaption.value.trim(),
      },
    )
    await loadVersions()
    // 新版本即当前版本，预览切过去并停在同一页
    previewVersionId.value = currentVersion.value?.artifact_version_id || ''
    previewSlideId.value = keptSlideId
    ElMessage.success(materialId ? '配图已更新并创建新版本' : '配图已移除并创建新版本')
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '配图未更新，原版本仍然保留'
  } finally {
    savingImage.value = false
  }
}

function applySlideImage() {
  if (!canApplyImage.value) return
  return saveSlideImage(imageMaterialId.value)
}

/**
 * 切换配图位置立即落一个新版本。
 *
 * AI 生成配图是"一步到位"的：生成完就直接设为该页配图。位置切换如果只改本地值、
 * 还要再点一次「应用配图并创建版本」，两套交互放在一起就会被当成"调整没反应"。
 * 这里让切换和生成一样立即生效；素材/图注的改动仍走按钮，避免误触创建版本。
 */
async function changeSlidePlacement(value) {
  imagePlacement.value = value
  const saved = previewSlide.value?.image
  if (!saved?.material_id || saved.placement === value) return
  if (!canAnnotate.value || savingImage.value) return
  await saveSlideImage(saved.material_id)
}

/**
 * 生成一张配图并直接绑到当前页。
 * 生成结果先按普通图片资料落库，再走同一条"应用配图并创建版本"逻辑，
 * 所以版本不可变、导出可复现这些性质都不变。
 */
async function generateSlideImageByAI() {
  if (!canGenerateImage.value) return
  const prompt = aiImagePrompt.value.trim()
  generatingImage.value = true
  errorMessage.value = ''
  try {
    const created = (await generateSlideImage(projectId.value, prompt)).data
    // 新图先进入候选列表与缩略图缓存，再交给同一套绑定逻辑
    await loadImageMaterials()
    await ensureImage(created.material_id)
    imageMaterialId.value = created.material_id
    await saveSlideImage(created.material_id)
    aiImagePrompt.value = ''
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '配图生成失败，请重试')
  } finally {
    generatingImage.value = false
  }
}

function removeSlideImage() {
  if (!canAnnotate.value || savingImage.value) return
  return saveSlideImage(null)
}

/**
 * 给这一版里还没配图的页面批量生成插图。
 *
 * 后端整批只创建一个新版本（一页一个版本会把版本历史冲垮），返回后按普通新版本
 * 刷新预览即可；单页生成失败会被跳过，不会让整批失败。
 */
async function illustrateMissingSlides() {
  if (!canIllustrate.value) return
  const keptSlideId = previewSlide.value?.slide_id
  // 数量要在请求前取：新版本已经配好图，之后再算就是 0
  const pending = missingImageCount.value
  illustrating.value = true
  errorMessage.value = ''
  try {
    await illustrateVersion(projectId.value, currentVersion.value.artifact_version_id)
    await loadVersions()
    previewVersionId.value = currentVersion.value?.artifact_version_id || ''
    previewSlideId.value = keptSlideId
    ElMessage.success(`已为 ${pending} 页生成配图并创建新版本`)
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '自动配图失败，请稍后重试')
  } finally {
    illustrating.value = false
  }
}

async function regenerateTarget() {
  if (!canRegenerate.value) return
  regenerating.value = true
  errorMessage.value = ''
  regenStageLabel.value = '正在准备重生成目标内容……'
  regenPercent.value = 0
  regenStageKey.value = ''
  regenStages.value = []
  regenStartedAt.value = new Date()
  try {
    let failure = null
    let created = null
    const client = new SSEClient(`/api/v1/projects/${projectId.value}/revisions/regenerate`, {
      onProgress: (payload) => {
        regenStageLabel.value = payload?.stage_label || payload?.label || regenStageLabel.value
        regenPercent.value = payload?.percent ?? regenPercent.value
        regenStageKey.value = payload?.stage || regenStageKey.value
        if (payload?.stages?.length) regenStages.value = payload.stages
      },
      onResult: (payload) => { created = payload },
      onServiceError: (details) => { failure = { response: { data: { error: details } } } },
      onError: () => { failure = streamFailure('REVISION_STREAM_DISCONNECTED', '生成连接中断，请重试') },
    })

    await client.connect({
      base_version_id: currentVersion.value.artifact_version_id,
      target_type: aiTargetType.value,
      target_id: aiTargetId.value,
      instruction: aiInstruction.value.trim(),
      // 只有真正选中了某一处才带锚点，保持"整页重生成"的请求形状不变。
      ...(aiField.value ? { field: aiField.value, index: aiIndex.value } : {}),
    })

    if (failure) throw failure
    if (!created) throw streamFailure('REVISION_STREAM_INCOMPLETE', '局部重生成未完成，请重试')

    aiInstruction.value = ''
    const keptSlideId = previewSlideId.value || aiTargetId.value
    await loadVersions()
    // 新版本即当前版本，预览跟着切过去，改动立刻可见。
    previewPinned.value = false
    previewVersionId.value = currentVersion.value?.artifact_version_id || ''
    previewSlideId.value = previewSlides.value.some(slide => slide.slide_id === keptSlideId)
      ? keptSlideId
      : (previewSlides.value[0]?.slide_id || '')
    ElMessage.success(`AI 已重生成目标并创建成果版本 v${created.version}`)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || 'AI 局部重生成失败，原版本仍然保留'
  } finally {
    regenerating.value = false
    regenStageLabel.value = ''
  }
}

async function restore(version) {
  try {
    await ElMessageBox.confirm(
      `恢复 v${version.version} 会创建一个新的成果版本，不会删除现有版本。`,
      '确认恢复成果版本',
      { confirmButtonText: '创建恢复版本', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const restored = (await restoreArtifactVersion(projectId.value, version.artifact_version_id)).data
    await loadVersions()
    ElMessage.success(`已创建恢复版本 v${restored.version}`)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '恢复失败，历史版本未改变'
  } finally {
    submitting.value = false
  }
}

async function downloadOutput(output) {
  try {
    const response = await downloadFile(output.file_id)
    const url = window.URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = outputName(output)
    link.click()
    window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '文件下载失败，请稍后重试')
  }
}

/** 产物可能来自生成任务的 outputs，也可能来自文件记录，两者的文件名字段不同。 */
function outputName(item) {
  return item.file_name || item.original_name || '生成文件'
}

async function loadVersionFiles() {
  if (!projectId.value || !previewVersion.value) {
    versionFiles.value = []
    return
  }
  try {
    const { data } = await fetchVersionFiles(
      projectId.value,
      previewVersion.value.artifact_version_id,
    )
    versionFiles.value = data || []
  } catch {
    // 产物加载失败不该影响版本与预览，留空即可
    versionFiles.value = []
  }
}

/**
 * 触发一次课件生成，并把任务 ID 写进 URL。
 *
 * 写进 URL 是为了复用已有的任务卡片与 SSE 订阅：`route.query.taskId` 变化会触发
 * `loadTask`，进度实时显示，任务结束后自动刷新版本与产物，不必整页重载。
 */
async function startGenerationFlow() {
  if (!projectId.value || generating.value) return
  generating.value = true
  errorMessage.value = ''
  try {
    const { data } = await startProjectGeneration(projectId.value)
    await router.replace({ query: { ...route.query, taskId: data.task_id } })
    ElMessage.success('已开始生成课件，进度会显示在下方')
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '生成任务创建失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

/** 只有选中非最新版本才算用户主动固定；选最新版本时仍是跟随状态。 */
function onPreviewVersionChange(value) {
  previewPinned.value = value !== currentVersion.value?.artifact_version_id
}

function openExports(version = currentVersion.value) {
  if (!version) return
  router.push({
    path: '/exports',
    query: { artifactVersionId: version.artifact_version_id, taskId: route.query.taskId },
  })
}

function exportVersion(version) {
  openExports(version)
}

// 预览默认跟随最新版本：生成、局部修改产生的新版本要能立刻看到。只有用户手动
// 选中了非最新版本才固定住；被固定的版本消失时退回最新版本。
watch(versions, () => {
  const latestId = currentVersion.value?.artifact_version_id || ''
  const stillExists = versions.value.some(
    item => item.artifact_version_id === previewVersionId.value,
  )
  if (!previewPinned.value || !previewVersionId.value || !stillExists) {
    previewPinned.value = false
    previewVersionId.value = latestId
  }
}, { immediate: true })

// 切换预览版本就重新拉取该版本的产物
watch(previewVersion, loadVersionFiles, { immediate: true })

// 生成按钮把任务 ID 写进 URL，这里接手订阅，不必整页重载
watch(() => route.query.taskId, loadTask)

onMounted(loadWorkspace)
watch([currentVersion, aiTargetType], () => {
  if (!targetOptions.value.some(item => item.value === aiTargetId.value)) {
    aiTargetId.value = targetOptions.value[0]?.value || ''
  }
}, { immediate: true })

// 锚点必须始终属于"正在预览的那一页"。目标被改到别处时，条目下标就失效了。
watch([aiTargetId, aiTargetType], () => {
  const anchorBelongsToPreview = aiTargetType.value === 'slide'
    && aiTargetId.value === previewSlide.value?.slide_id
  if (!anchorBelongsToPreview) clearElementAnchor()
})

// 切到别的版本时原页面可能不存在，回到该版本的第一页。
watch(previewVersion, () => {
  if (!previewSlides.value.some(slide => slide.slide_id === previewSlideId.value)) {
    previewSlideId.value = previewSlides.value[0]?.slide_id || ''
  }
})

// 预览用到的配图按需拉取，切换版本时补齐新出现的素材。
watch(previewSlides, slides => {
  ensureSlideImages(slides)
}, { immediate: true })

// 配图表单跟随当前预览页，切页或切版本后不残留上一页的设置。
watch(previewSlide, slide => {
  syncImageForm(slide)
}, { immediate: true })

// 选中的素材也取回缩略图，方便确认选对了哪一张
watch(imageMaterialId, materialId => {
  ensureImage(materialId)
}, { immediate: true })

// 候选来自本项目上传的图片资料
watch(projectId, () => {
  loadImageMaterials()
}, { immediate: true })

onBeforeUnmount(() => {
  taskStream?.disconnect()
  for (const url of Object.values(slideImageUrls.value)) window.URL.revokeObjectURL(url)
  slideImageUrls.value = {}
})
</script>

<style scoped>
.task-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.task-summary > div,
.version-main {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.task-summary strong,
.version-main strong,
.version-main span,
.version-main small {
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-summary strong,
.version-heading {
  white-space: nowrap;
}

.success-text { color: #16834b; }

.revision-section { display: grid; gap: 14px; }
.ai-revision-form, .manual-revision-form { display: grid; gap: 14px; }

.preview-section { display: grid; gap: 14px; }
.outputs-section { display: grid; gap: 14px; }
.outputs-empty { margin: 0; color: #64748b; font-size: 13px; }
.preview-version { width: 220px; }

.preview-body {
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.preview-rail {
  display: grid;
  gap: 6px;
  max-height: 460px;
  overflow-y: auto;
}

.rail-item {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  padding: 8px;
  border: 1px solid #e6eaf0;
  border-radius: 8px;
  background: #fff;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.rail-item:hover { border-color: #c7dcff; background: #f6f9ff; }
.rail-item.is-current { border-color: #1463ff; background: #eef4ff; }

.rail-order {
  display: grid;
  place-items: center;
  aspect-ratio: 1;
  border-radius: 6px;
  background: #eef4ff;
  color: #1463ff;
  font-size: 12px;
  font-weight: 700;
}

.rail-title {
  overflow: hidden;
  color: #334155;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-main { min-width: 0; }

.preview-instruction {
  display: grid;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid #e6eaf0;
}

.preview-image {
  display: grid;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid #e6eaf0;
}

.preview-image-row {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.preview-image-pick {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 56px;
  gap: 10px;
  align-items: center;
}

.preview-image-thumb {
  width: 56px;
  height: 40px;
  object-fit: cover;
  border: 1px solid #e6eaf0;
  border-radius: 6px;
  background: #f8fafc;
}

.preview-image-hint {
  margin: 0;
  color: #94a3b8;
  font-size: 12px;
}

.option-hint {
  float: right;
  margin-left: 16px;
  color: #94a3b8;
  font-size: 12px;
}
.target-selectors { display: grid; grid-template-columns: auto minmax(240px, 1fr); gap: 12px; align-items: center; }

.field-label {
  color: #334155;
  font-size: 14px;
  font-weight: 600;
}

.revision-actions,
.version-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.patch-preview { margin-top: 2px; }

.version-list,
.output-grid { display: grid; gap: 4px; }

.version-row,
.output-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  padding: 14px 0;
  border-bottom: 1px solid #e6eaf0;
}

.version-row:last-child,
.output-item:last-child { border-bottom: 0; }

.version-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.version-main span,
.version-main small,
.output-content span { color: #64748b; }

.output-item { grid-template-columns: 52px minmax(0, 1fr) auto; }

.output-icon {
  display: grid;
  place-items: center;
  aspect-ratio: 1;
  border-radius: 6px;
  background: #eef4ff;
  color: #1463ff;
  font-size: 12px;
  font-weight: 700;
}

.output-content { display: grid; gap: 4px; min-width: 0; }

@media (max-width: 900px) {
  .preview-body { grid-template-columns: 1fr; }
  .preview-version { width: 100%; }
  .preview-image-row { grid-template-columns: 1fr; }
  .preview-rail {
    grid-auto-flow: column;
    grid-auto-columns: 148px;
    max-height: none;
    overflow-x: auto;
    overflow-y: hidden;
    padding-bottom: 4px;
  }
}

@media (max-width: 720px) {
  .task-summary { grid-template-columns: 1fr 1fr; }
  .version-row { grid-template-columns: 1fr; }
  .output-item { grid-template-columns: 44px minmax(0, 1fr); }
  .output-item .el-button { grid-column: 2; justify-self: start; }
  .target-selectors { grid-template-columns: 1fr; }
}
</style>
