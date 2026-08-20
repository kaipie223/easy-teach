<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>知识库管理</h1>
        <p>导入可复用教学资料，启用后建立索引，教师检索时只使用已就绪文档。</p>
      </div>
      <el-button :loading="loading" text @click="loadDocuments">
        <el-icon><Refresh /></el-icon>
        刷新状态
      </el-button>
    </header>

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
          <h2>导入知识库文档</h2>
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
      <el-empty v-else-if="!documents.length" description="还没有知识库文档">
        <el-button type="primary" @click="fileInput?.click()">
          <el-icon><Upload /></el-icon>
          导入第一份文档
        </el-button>
      </el-empty>
      <div v-else class="table-wrap">
        <table class="data-table knowledge-table">
          <thead>
            <tr>
              <th>文档</th>
              <th>集合</th>
              <th>类型</th>
              <th>分段</th>
              <th>索引状态</th>
              <th>启用</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="document in documents" :key="document.document_id">
              <td>
                <strong class="document-title" :title="document.title">{{ document.title }}</strong>
                <span class="document-id">{{ document.document_id }}</span>
                <span v-if="document.error_message" class="document-error">{{ document.error_message }}</span>
              </td>
              <td>{{ document.collection_id }}</td>
              <td>{{ fileTypeLabel(document.file_type) }}</td>
              <td>{{ document.chunk_count }}</td>
              <td>
                <span :class="['status-pill', indexStatusClass(document.index_status)]">
                  {{ indexStatusLabel(document.index_status) }}
                </span>
              </td>
              <td>
                <el-switch
                  :model-value="document.enabled"
                  :loading="document.updating"
                  @change="toggleDocument(document, $event)"
                />
              </td>
              <td class="link-actions">
                <el-button
                  link
                  type="primary"
                  :loading="document.indexing"
                  :disabled="!document.enabled"
                  @click="indexDocument(document)"
                >
                  <el-icon><Refresh /></el-icon>
                  索引
                </el-button>
                <el-button link type="danger" @click="removeDocument(document)">
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
      <el-empty v-if="searched && !results.length" description="没有命中已就绪的知识片段" />
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
  Delete,
  FolderOpened,
  Refresh,
  Search,
  Upload,
} from '@element-plus/icons-vue'
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
    Object.assign(document, {
      index_status: 'failed',
      error_message: apiErrorMessage(error, '文档索引失败，请重试'),
    })
    ElMessage.error(document.error_message)
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

function locatorLabel(locator = {}) {
  if (locator.page) return `第 ${locator.page} 页`
  if (locator.slide) return `第 ${locator.slide} 页`
  if (locator.paragraph) return `第 ${locator.paragraph} 段`
  if (locator.offset !== undefined) return `文本位置 ${locator.offset}`
  return '来源定位'
}

function apiErrorMessage(error, fallback) {
  return error.response?.data?.error?.message || error.message || fallback
}
</script>

<style scoped>
.import-form {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(160px, 0.55fr) auto;
  gap: 14px;
  align-items: end;
  max-width: 900px;
}

.form-field {
  display: grid;
  gap: 7px;
}

.form-field label,
.field-label,
.search-form label {
  color: #475569;
  font-size: 13px;
  font-weight: 700;
}

.enabled-field {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
}

.import-actions,
.search-form {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
}

.selected-file {
  min-width: 0;
  max-width: 420px;
  overflow: hidden;
  color: #475569;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inline-alert {
  margin-top: 14px;
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

.knowledge-table {
  min-width: 900px;
}

.document-title {
  display: block;
  max-width: 240px;
  overflow: hidden;
  color: #0f172a;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-id {
  display: block;
  margin-top: 4px;
  color: #94a3b8;
  font-size: 12px;
}

.document-error {
  display: block;
  max-width: 280px;
  margin-top: 5px;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.4;
}

.search-form {
  max-width: 760px;
}

.search-form .el-input {
  flex: 1;
}

.result-list {
  display: grid;
  gap: 10px;
  margin-top: 18px;
}

.result-row {
  padding: 14px 16px;
  border-left: 3px solid #1463ff;
  background: #f8fafc;
}

.result-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #0f172a;
}

.result-heading span {
  color: #64748b;
  font-size: 12px;
}

.result-row p {
  margin: 8px 0;
  color: #475569;
  line-height: 1.65;
}

.result-row small {
  color: #64748b;
}

@media (max-width: 760px) {
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
