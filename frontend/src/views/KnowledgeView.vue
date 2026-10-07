<template>
  <div class="page-stack">
    <AppPageHeader
      title="我的知识库"
      subtitle="管理仅属于当前账号的教学资料；生成时只检索已启用并完成索引的文档。"
    >
      <template #actions>
        <el-button :loading="loading" text @click="loadDocuments">
          <el-icon><Refresh /></el-icon>
          刷新状态
        </el-button>
      </template>
    </AppPageHeader>

    <el-alert
      v-if="pageError"
      :title="pageError"
      type="error"
      show-icon
      :closable="false"
    />

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>导入我的文档</h2>
          <p>支持 PDF、Word、PPT、TXT 和 Markdown；导入完成后仍需建立向量索引。</p>
        </div>
      </div>
      <div class="import-form">
        <div class="form-field">
          <label for="knowledge-title">文档标题</label>
          <el-input
            id="knowledge-title"
            v-model="title"
            maxlength="255"
            placeholder="留空时使用文件名"
            :disabled="uploading"
          />
        </div>
        <div class="form-field collection-field">
          <label for="knowledge-collection">集合</label>
          <el-input
            id="knowledge-collection"
            v-model="collectionId"
            maxlength="128"
            :disabled="uploading"
          />
        </div>
        <div class="form-field enabled-field">
          <span class="field-label">导入后启用</span>
          <el-switch v-model="enabled" :disabled="uploading" />
        </div>
      </div>
      <div class="import-actions">
        <input
          ref="fileInput"
          class="visually-hidden"
          type="file"
          accept=".pdf,.docx,.pptx,.txt,.md"
          @change="selectFile"
        />
        <el-button @click="fileInput?.click()" :disabled="uploading">
          <el-icon><FolderOpened /></el-icon>
          选择文档
        </el-button>
        <span class="selected-file" :title="selectedFile?.name || ''">
          {{ selectedFile?.name || '尚未选择文档' }}
        </span>
        <el-button type="primary" :loading="uploading" :disabled="!selectedFile" @click="uploadDocument">
          <el-icon><Upload /></el-icon>
          导入文档
        </el-button>
      </div>
      <el-alert
        v-if="uploadError"
        :title="uploadError"
        type="error"
        show-icon
        :closable="false"
        class="inline-alert"
      />
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>文档状态</h2>
          <p>停用或删除后不会作为新的检索来源；修改启用状态后需要重新索引。</p>
        </div>
        <el-button type="primary" :loading="indexing" :disabled="!documents.length" @click="rebuildIndex">
          <el-icon><Refresh /></el-icon>
          建立索引
        </el-button>
      </div>

      <el-skeleton v-if="loading" :rows="5" animated />
      <AppEmptyState
        v-else-if="!documents.length"
        :icon="Collection"
        title="知识库还是空的"
        description="导入讲义、题库或课标文件，索引后即可作为生成的证据来源。"
      >
        <el-button type="primary" @click="fileInput?.click()">
          <el-icon><Upload /></el-icon>
          导入第一份文档
        </el-button>
      </AppEmptyState>
      <template v-else>
        <el-alert
          v-if="indexing || documents.some(document => document.indexing)"
          title="正在生成中文向量索引"
          description="首次运行需要下载轻量向量模型，完成前请不要重复提交。"
          type="info"
          show-icon
          :closable="false"
          class="indexing-alert"
        />
        <ul class="document-list motion-stagger">
          <li v-for="document in documents" :key="document.document_id" class="document-row">
            <span class="row-mark" :class="`is-${document.file_type}`" aria-hidden="true">
              <el-icon><component :is="fileTypeIcon(document.file_type)" /></el-icon>
            </span>

            <div class="document-main">
              <div class="document-heading">
                <strong class="document-title" :title="document.title">{{ document.title }}</strong>
                <span :class="['status-pill', indexStatusClass(document.index_status)]">
                  {{ indexStatusLabel(document.index_status) }}
                </span>
              </div>
              <!-- 内部 document_id 不再展示：它对教师没有意义 -->
              <p class="document-meta">
                <span>{{ document.collection_id }}</span>
                <span class="dot" aria-hidden="true">·</span>
                <span>{{ fileTypeLabel(document.file_type) }}</span>
                <span class="dot" aria-hidden="true">·</span>
                <span class="text-tabular">{{ document.chunk_count }} 个分段</span>
              </p>
              <p v-if="document.error_message" class="document-error">{{ document.error_message }}</p>
            </div>

            <div class="row-actions">
              <label class="enable-toggle">
                <span class="binding-label">启用</span>
                <el-switch
                  :model-value="document.enabled"
                  :loading="document.updating"
                  @change="toggleDocument(document, $event)"
                />
              </label>
              <el-button
                text
                :loading="document.indexing"
                :disabled="!document.enabled"
                @click="indexDocument(document)"
              >
                <el-icon><Refresh /></el-icon>
                {{ document.index_status === 'failed' ? '重试' : '重建' }}
              </el-button>
              <!-- 删除收进"更多"：破坏性操作不与常规操作并排 -->
              <el-dropdown trigger="click" @command="command => handleRowCommand(command, document)">
                <el-button text aria-label="更多操作">
                  <el-icon><MoreFilled /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="delete">
                      <span class="danger-item">删除文档</span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </li>
        </ul>
      </template>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>检索验证</h2>
          <p>输入一个已知知识点，确认结果包含来源文档和定位信息。</p>
        </div>
      </div>
      <div class="search-form">
        <label for="knowledge-query">检索内容</label>
        <el-input
          id="knowledge-query"
          v-model="query"
          placeholder="例如：TCP 三次握手"
          @keydown.enter="search"
        />
        <el-button type="primary" :loading="searching" :disabled="!query.trim()" @click="search">
          <el-icon><Search /></el-icon>
          检索
        </el-button>
      </div>
      <p v-if="searched && !results.length" class="list-hint">
        没有命中已就绪的知识片段——确认文档已导入并完成索引。
      </p>
      <div v-else-if="results.length" class="result-list">
        <article v-for="(result, index) in results" :key="`${result.evidence_id || result.source}-${index}`" class="result-row">
          <div class="result-heading">
            <strong>{{ result.source }}</strong>
            <span>{{ result.score.toFixed(3) }}</span>
          </div>
          <p>{{ result.content }}</p>
          <small v-if="result.locator && Object.keys(result.locator).length">
            {{ locatorLabel(result.locator) }}
          </small>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Collection,
  Document,
  FolderOpened,
  Monitor,
  MoreFilled,
  Picture,
  Refresh,
  Search,
  Upload,
  VideoCamera,
} from '@element-plus/icons-vue'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import {
  deleteKnowledgeDocument,
  fetchKnowledgeDocuments,
  indexKnowledgeDocument,
  rebuildKnowledgeIndex,
  searchKnowledge,
  updateKnowledgeDocument,
  uploadKnowledgeDocument,
} from '@/api'

const documents = ref([])
const loading = ref(false)
const indexing = ref(false)
const uploading = ref(false)
const searching = ref(false)
const pageError = ref('')
const uploadError = ref('')
const selectedFile = ref(null)
const fileInput = ref(null)
const title = ref('')
const collectionId = ref('default')
const enabled = ref(true)
const query = ref('')
const results = ref([])
const searched = ref(false)

onMounted(loadDocuments)

async function loadDocuments() {
  loading.value = true
  pageError.value = ''
  try {
    const response = await fetchKnowledgeDocuments()
    documents.value = (response.data || []).map(document => ({
      ...document,
      updating: false,
      indexing: false,
    }))
  } catch (error) {
    pageError.value = apiErrorMessage(error, '知识库文档加载失败，请重试')
  } finally {
    loading.value = false
  }
}

function selectFile(event) {
  selectedFile.value = event.target.files?.[0] || null
  event.target.value = ''
  uploadError.value = ''
  if (selectedFile.value && !title.value) {
    title.value = selectedFile.value.name.replace(/\.[^.]+$/, '')
  }
}

async function uploadDocument() {
  if (!selectedFile.value || uploading.value) return
  uploadError.value = ''
  uploading.value = true
  try {
    await uploadKnowledgeDocument(selectedFile.value, {
      title: title.value.trim(),
      collectionId: collectionId.value.trim(),
      enabled: enabled.value,
    })
    selectedFile.value = null
    title.value = ''
    await loadDocuments()
    ElMessage.success('知识库文档已导入')
  } catch (error) {
    uploadError.value = apiErrorMessage(error, '知识库文档导入失败，请重试')
  } finally {
    uploading.value = false
  }
}

async function toggleDocument(document, nextEnabled) {
  const previous = document.enabled
  document.updating = true
  try {
    const response = await updateKnowledgeDocument(document.document_id, { enabled: nextEnabled })
    Object.assign(document, response.data)
    ElMessage.success(nextEnabled ? '文档已启用，请重新索引' : '文档已停用')
  } catch (error) {
    document.enabled = previous
    ElMessage.error(apiErrorMessage(error, '文档状态更新失败，请重试'))
  } finally {
    document.updating = false
  }
}

async function rebuildIndex() {
  if (indexing.value) return
  indexing.value = true
  pageError.value = ''
  try {
    await rebuildKnowledgeIndex()
    await loadDocuments()
    ElMessage.success('知识库索引已建立')
  } catch (error) {
    pageError.value = apiErrorMessage(error, '索引建立失败，请检查模型和网络后重试')
    await loadDocuments()
  } finally {
    indexing.value = false
  }
}

async function indexDocument(document) {
  if (document.indexing || !document.enabled) return
  document.indexing = true
  try {
    await indexKnowledgeDocument(document.document_id)
    await loadDocuments()
    ElMessage.success(`“${document.title}”已完成索引`)
  } catch (error) {
    const message = apiErrorMessage(error, '文档索引失败，请重试')
    pageError.value = message
    await loadDocuments()
    ElMessage.error(message)
  } finally {
    document.indexing = false
  }
}

async function removeDocument(document) {
  try {
    await ElMessageBox.confirm(
      `删除“${document.title}”？它将不再参与新的知识检索。`,
      '删除知识库文档',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteKnowledgeDocument(document.document_id)
    await loadDocuments()
    ElMessage.success('知识库文档已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(apiErrorMessage(error, '文档删除失败，请重试'))
    }
  }
}

async function search() {
  if (!query.value.trim() || searching.value) return
  searching.value = true
  searched.value = true
  try {
    const response = await searchKnowledge(query.value.trim(), 5)
    results.value = response.data || []
  } catch (error) {
    results.value = []
    ElMessage.error(apiErrorMessage(error, '知识检索失败，请重试'))
  } finally {
    searching.value = false
  }
}

function indexStatusLabel(status) {
  return {
    pending: '待索引',
    indexing: '索引中',
    ready: '已就绪',
    failed: '失败，需重试',
  }[status] || status || '未知状态'
}

function indexStatusClass(status) {
  return {
    pending: 'pending',
    indexing: 'running',
    ready: 'done',
    failed: 'failed',
  }[status] || 'pending'
}

function fileTypeLabel(fileType) {
  return {
    pdf: 'PDF',
    word: 'Word',
    ppt: 'PPT',
    text: '文本',
  }[fileType] || fileType
}

/** 文档类型图标：与列表里的类型色标配合，同类文件一眼可辨。 */
const FILE_TYPE_ICONS = {
  pdf: Document,
  word: Document,
  ppt: Monitor,
  text: Picture,
  image: Picture,
  video: VideoCamera,
}

function fileTypeIcon(fileType) {
  return FILE_TYPE_ICONS[fileType] || Document
}

/** 行内"更多"菜单：破坏性操作与常规操作分开。 */
function handleRowCommand(command, document) {
  if (command === 'delete') removeDocument(document)
}

function locatorLabel(locator = {}) {
  if (locator.page) return `第 ${locator.page} 页`
  if (locator.slide) return `第 ${locator.slide} 页`
  if (locator.paragraph) return `第 ${locator.paragraph} 段`
  if (locator.offset !== undefined) return `文本位置 ${locator.offset}`
  return '来源定位'
}

function apiErrorMessage(error, fallback) {
  const apiError = error.response?.data?.error
  const message = apiError?.message || error.message || fallback
  const action = apiError?.suggested_action
  return action && action !== message ? `${message}；${action}` : message
}
</script>

<style scoped>
.import-form {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(160px, 0.55fr) auto;
  gap: var(--space-3);
  align-items: end;
  max-width: 900px;
}

.form-field {
  display: grid;
  gap: var(--space-2);
}

.form-field label,
.field-label,
.search-form label {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 700;
}

.enabled-field {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 32px;
}

.import-actions,
.search-form {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.selected-file {
  min-width: 0;
  max-width: 420px;
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inline-alert {
  margin-top: var(--space-3);
}

.indexing-alert {
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

/* ── 文档列表：列表行取代表格（7 列表格在窄屏只能横向滚动） ─── */
.document-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.document-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  gap: var(--space-4);
  align-items: start;
  padding: var(--space-4) var(--space-2);
  border-bottom: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  transition: background-color var(--duration-fast) var(--ease-standard);
}

.document-row:last-child { border-bottom: 0; }

.document-row:hover { background: var(--neutral-50); }

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
.row-mark.is-text { color: var(--file-image); }
.row-mark.is-image { color: var(--file-image); }
.row-mark.is-video { color: var(--file-video); }

.document-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.document-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.document-title {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.dot { color: var(--neutral-400); }

.document-error {
  color: var(--danger-600);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  justify-content: flex-end;
}

.enable-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}

.binding-label {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.danger-item { color: var(--danger-600); }

.search-form {
  max-width: 760px;
}

.search-form .el-input {
  flex: 1;
}

.result-list {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.result-row {
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--border-brand);
  background: var(--bg-surface-sunken);
}

.result-heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--text-primary);
}

.result-heading span {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.result-row p {
  margin: var(--space-2) 0;
  color: var(--text-secondary);
  line-height: 1.65;
}

.result-row small {
  color: var(--text-tertiary);
}

@media (max-width: 768px) {
  /* 窄屏：类型标与标题同排，启用开关与操作换到下一行 */
  .document-row {
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

  .document-main { grid-area: main; }

  .row-actions {
    grid-area: actions;
    gap: var(--space-4);
    justify-content: flex-start;
  }

  .row-actions :deep(.el-button) {
    min-width: 36px;
    height: 36px;
  }

  .document-meta { flex-wrap: wrap; }

  .import-form {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .import-actions,
  .search-form {
    align-items: stretch;
    flex-direction: column;
  }

  .import-actions .el-button,
  .search-form .el-button,
  .search-form .el-input {
    width: 100%;
  }

  .selected-file {
    width: 100%;
    max-width: none;
  }
}
</style>
