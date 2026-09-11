<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>导出与版本</h1>
        <p>选择具体成果版本导出，文件内容不会随当前版本变化。</p>
      </div>
      <el-button :loading="loading" @click="loadWorkspace">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </header>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-skeleton v-if="loading && !versions.length" :rows="6" animated />

    <template v-else-if="projectId && versions.length">
      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>导出版本</h2>
            <p>导出任务按版本和格式幂等保存，重复刷新不会丢失状态。</p>
          </div>
        </div>
        <label class="field-label" for="export-version">成果版本</label>
        <el-select id="export-version" v-model="selectedVersionId" class="version-select" @change="loadExports">
          <el-option
            v-for="version in versions"
            :key="version.artifact_version_id"
            :label="`v${version.version} · ${version.summary}`"
            :value="version.artifact_version_id"
          />
        </el-select>
        <div class="export-actions">
          <el-button type="primary" :loading="exporting" @click="createExports">
            导出 PPT、Word、PDF 和互动内容
          </el-button>
          <el-button plain @click="openEditor">返回成果编辑</el-button>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>导出记录</h2>
            <p v-if="selectedVersion">当前版本：v{{ selectedVersion.version }} · {{ selectedVersion.summary }}</p>
          </div>
        </div>
        <el-empty v-if="!exports.length" description="该版本还没有导出记录" />
        <div v-else class="export-list">
          <article v-for="item in exports" :key="item.export_id" class="export-row">
            <div class="export-main">
              <div class="export-heading">
                <strong>{{ formatLabel(item.format) }}</strong>
                <el-tag :type="statusType(item.status)" size="small">{{ statusLabel(item.status) }}</el-tag>
              </div>
              <span>{{ item.file_name || '文件生成中' }}</span>
              <small>{{ item.size_bytes ? `${Math.ceil(item.size_bytes / 1024)} KB` : item.error || '等待任务完成' }}</small>
            </div>
            <el-button
              v-if="item.status === 'completed'"
              type="primary"
              plain
              @click="download(item)"
            >
              <el-icon><Download /></el-icon>
              下载
            </el-button>
            <el-button v-else-if="item.status === 'failed'" plain @click="retryExport(item)">
              重试导出
            </el-button>
          </article>
        </div>
      </section>
    </template>

    <el-empty v-else description="请先生成成果版本，再进入导出页面" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Download, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  createVersionExports,
  downloadExport,
  fetchArtifactVersions,
  fetchProjectExports,
  getApiErrorMessage,
} from '@/api'
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
let pollTimer = null

const selectedVersion = computed(() => versions.value.find((version) => version.artifact_version_id === selectedVersionId.value))

function statusLabel(status) {
  return { pending: '排队中', processing: '导出中', completed: '已完成', failed: '失败' }[status] || '未知'
}

function statusType(status) {
  return { completed: 'success', failed: 'danger', processing: 'warning' }[status] || 'info'
}

function formatLabel(format) {
  return { pptx: 'PPTX 演示文稿', docx: 'DOCX 教案', pdf: 'PDF 教案', html: 'HTML5 互动内容' }[format] || format.toUpperCase()
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
  if (exports.value.some((item) => ['pending', 'processing'].includes(item.status))) pollExports()
}

function pollExports() {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(async () => {
    try {
      await loadExports()
    } catch (error) {
      errorMessage.value = error.response?.data?.error?.message || '导出状态刷新失败，请手动刷新'
    }
  }, 1200)
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
    pollExports()
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
    await createVersionExports(projectId.value, selectedVersionId.value, [item.format], true)
    await loadExports()
    ElMessage.success(`已重新创建 ${formatLabel(item.format)} 导出任务`)
  } catch (error) {
    errorMessage.value = await getApiErrorMessage(error, '导出任务重试失败')
  } finally {
    exporting.value = false
  }
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

onMounted(loadWorkspace)
onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer)
})
</script>

<style scoped>
.field-label {
  display: block;
  margin-bottom: 8px;
  color: #334155;
  font-size: 14px;
  font-weight: 600;
}

.version-select { width: min(100%, 620px); }

.export-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.export-list { display: grid; gap: 4px; }

.export-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  padding: 14px 0;
  border-bottom: 1px solid #e6eaf0;
}

.export-row:last-child { border-bottom: 0; }

.export-main { display: grid; gap: 5px; min-width: 0; }
.export-heading { display: flex; align-items: center; gap: 8px; }
.export-main span,
.export-main small { color: #64748b; overflow: hidden; text-overflow: ellipsis; }

@media (max-width: 720px) {
  .export-row { grid-template-columns: 1fr; }
  .export-row .el-button { justify-self: start; }
}
</style>
