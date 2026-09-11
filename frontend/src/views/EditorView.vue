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
        <el-button type="primary" :disabled="!currentVersion" @click="openExports">
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
            {{ statusLabel }} · {{ task.progress }}%
          </el-tag>
        </div>
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
import { Download, MagicStick, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  applyRevision,
  downloadFile,
  fetchArtifactVersions,
  getApiErrorMessage,
  getTaskStatus,
  interpretRevision,
  regenerateArtifactTarget,
  restoreArtifactVersion,
} from '@/api'
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
let pollTimer = null

const currentVersion = computed(() => versions.value[0] || null)
const statusLabel = computed(() => ({
  pending: '排队中',
  processing: '生成中',
  completed: '已完成',
  failed: '失败',
}[task.value?.status] || '未知'))
const statusClass = computed(() => task.value?.status === 'completed' ? 'success-text' : 'muted')
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
const canRegenerate = computed(() => Boolean(
  currentVersion.value && aiTargetId.value && aiInstruction.value.trim() && !regenerating.value,
))

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

async function loadTask() {
  const taskId = String(route.query.taskId || '')
  if (!taskId) {
    task.value = null
    return
  }
  task.value = (await getTaskStatus(taskId)).data
  if (['pending', 'processing'].includes(task.value.status)) pollTask(taskId)
}

function pollTask(taskId) {
  const tick = async () => {
    try {
      task.value = (await getTaskStatus(taskId)).data
      if (['pending', 'processing'].includes(task.value.status)) pollTimer = window.setTimeout(tick, 1000)
      else await loadVersions()
    } catch (error) {
      errorMessage.value = error.response?.data?.error?.message || '生成状态获取失败，版本数据仍可刷新查看'
    }
  }
  pollTimer = window.setTimeout(tick, 1000)
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

async function regenerateTarget() {
  if (!canRegenerate.value) return
  regenerating.value = true
  errorMessage.value = ''
  try {
    const created = (await regenerateArtifactTarget(projectId.value, {
      base_version_id: currentVersion.value.artifact_version_id,
      target_type: aiTargetType.value,
      target_id: aiTargetId.value,
      instruction: aiInstruction.value.trim(),
    })).data
    aiInstruction.value = ''
    await loadVersions()
    ElMessage.success(`AI 已重生成目标并创建成果版本 v${created.version}`)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || 'AI 局部重生成失败，原版本仍然保留'
  } finally {
    regenerating.value = false
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
    link.download = output.file_name
    link.click()
    window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '文件下载失败，请稍后重试')
  }
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

onMounted(loadWorkspace)
watch([currentVersion, aiTargetType], () => {
  if (!targetOptions.value.some(item => item.value === aiTargetId.value)) {
    aiTargetId.value = targetOptions.value[0]?.value || ''
  }
}, { immediate: true })
onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer)
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

@media (max-width: 720px) {
  .task-summary { grid-template-columns: 1fr 1fr; }
  .version-row { grid-template-columns: 1fr; }
  .output-item { grid-template-columns: 44px minmax(0, 1fr); }
  .output-item .el-button { grid-column: 2; justify-self: start; }
  .target-selectors { grid-template-columns: 1fr; }
}
</style>
