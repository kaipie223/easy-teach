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

    <!-- 库概览：答辩时一眼说清"这个专业数据库有多大"。数字全部由已入库文档聚合，
         不做任何估算；抽字率是唯一能暴露"整本是扫描件"的指标。 -->
    <section v-if="documents.length && !loading" class="section-card library-overview">
      <div class="section-header">
        <div>
          <h2>库概览</h2>
          <p>当前账号可检索的教材规模；生成课件时只会命中已启用且完成索引的文档。</p>
        </div>
      </div>
      <div class="overview-grid">
        <div class="overview-item">
          <span class="overview-value text-tabular">{{ libraryStats.total }}</span>
          <span class="overview-label">本教材</span>
        </div>
        <div class="overview-item">
          <span class="overview-value text-tabular">{{ libraryStats.pages.toLocaleString() }}</span>
          <span class="overview-label">页原文</span>
        </div>
        <div class="overview-item">
          <span class="overview-value text-tabular">{{ libraryStats.chunks.toLocaleString() }}</span>
          <span class="overview-label">个知识块</span>
        </div>
        <div class="overview-item">
          <span class="overview-value text-tabular">{{ libraryStats.ready }}/{{ libraryStats.total }}</span>
          <span class="overview-label">已建索引</span>
        </div>
        <div class="overview-item">
          <span class="overview-value text-tabular">{{ libraryStats.coverage }}%</span>
          <span class="overview-label">平均抽字率</span>
        </div>
      </div>
      <el-alert
        v-if="libraryStats.suspects"
        class="overview-alert"
        type="warning"
        :closable="false"
        show-icon
        :title="`有 ${libraryStats.suspects} 本抽字率偏低：多半是扫描件，导入成功却检索不到内容，需要先 OCR 再入库`"
      />
    </section>

    <!-- 索引进度：重建跑在后台，页面上拿不到中间状态，所以进度来自轮询。
         没有它，教师只能看到一句"正在建立索引"，既不知道在处理哪一本，也无从判断
         是卡住了还是在正常推进。 -->
    <section v-if="showIndexProgress" class="section-card">
      <div class="section-header">
        <div>
          <h2>{{ indexProgressTitle }}</h2>
          <p>{{ indexProgressHint }}</p>
        </div>
      </div>
      <el-progress
        :percentage="indexProgress.percent"
        :status="indexProgress.stalled ? 'exception' : undefined"
        :stroke-width="14"
      />
      <div class="progress-facts">
        <span>
          正在处理：
          <strong>{{ currentDocumentLabel }}</strong>
        </span>
        <span v-if="indexProgress.current_chunks_total" class="text-tabular">
          本文件已写入 {{ indexProgress.current_chunks_written }} / {{ indexProgress.current_chunks_total }} 块
        </span>
        <span class="text-tabular">
          已建索引 {{ indexProgress.ready_documents }} / {{ indexProgress.total_documents }} 本
        </span>
        <span v-if="indexProgress.total_chunks" class="text-tabular">
          知识块 {{ indexProgress.written_chunks }} / {{ indexProgress.total_chunks }}
        </span>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>文档状态</h2>
          <p>停用或删除后不会作为新的检索来源，重新索引时会清掉它们已建的索引；修改启用状态后需要重新索引。</p>
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
          v-if="documents.some(document => document.indexing)"
          title="正在为这一份文档重新索引"
          description="只会重新处理这一份文档，其它已建好的索引不受影响，进度见上方进度条。"
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
                <!-- 首建/全量时所有文档都是"索引中"，只标出真正在处理的那一本，
                     否则教师看到一整列"索引中"仍然不知道进度在哪 -->
                <el-tag
                  v-if="document.document_id === currentIndexingDocumentId"
                  type="primary"
                  size="small"
                  effect="dark"
                >
                  处理中
                </el-tag>
              </div>
              <!-- 内部 document_id 不再展示：它对教师没有意义 -->
              <p class="document-meta">
                <span>{{ document.collection_id }}</span>
                <span class="dot" aria-hidden="true">·</span>
                <span>{{ fileTypeLabel(document.file_type) }}</span>
                <span class="dot" aria-hidden="true">·</span>
                <span class="text-tabular">{{ document.chunk_count }} 个分段</span>
                <template v-if="document.page_count">
                  <span class="dot" aria-hidden="true">·</span>
                  <span class="text-tabular">{{ document.page_count }} 页</span>
                  <span
                    class="coverage-tag"
                    :class="{ 'is-low': document.text_coverage < 0.9 }"
                    :title="`${document.text_pages}/${document.page_count} 页能抽出文字`"
                  >
                    抽字率 {{ Math.round((document.text_coverage || 0) * 100) }}%
                  </span>
                </template>
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
                {{ document.index_status === 'failed' ? '重试' : '重新索引' }}
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
import { computed, onMounted, onUnmounted, ref } from 'vue'
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
  fetchKnowledgeIndexProgress,
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
// 索引进度：由后端从文档状态推导，所以刷新页面后还能接着显示，而不是回到"未知"
const indexProgress = ref(null)
// 本页发起、正在等待结束的那次重建：'知识库' 或书名（用于完成后的文案）。
// 请求只代表"已启动"，所以"跑完没有"必须由进度轮询判定，完成文案也只能等到那时再说。
const pendingIndexJob = ref(null)
let progressTimer = null
let idleStreak = 0

// 库概览：全部来自已入库文档的字段（页数、抽字率由后端在导入时记进 metadata），
// 前端只做聚合，不估算。suspects 只统计"页数够多却抽不出字"的书 —— 短文档本来就
// 可能没几页可抽，不该被标成扫描件。
const libraryStats = computed(() => {
  const total = documents.value.length
  const pages = documents.value.reduce((sum, item) => sum + (item.page_count || 0), 0)
  const textPages = documents.value.reduce((sum, item) => sum + (item.text_pages || 0), 0)
  const chunks = documents.value.reduce((sum, item) => sum + (item.chunk_count || 0), 0)
  const ready = documents.value.filter(item => item.index_status === 'ready').length
  const suspects = documents.value.filter(
    item => (item.page_count || 0) >= 20 && (item.text_coverage || 0) < 0.9,
  ).length
  return {
    total,
    pages,
    chunks,
    ready,
    coverage: pages ? Math.round((textPages / pages) * 100) : 0,
    suspects,
  }
})

// ── 索引进度 ───────────────────────────────────────────
//
// 重建在服务端后台线程里跑：启动接口毫秒级返回，结束（成功或失败）没有任何回调，
// 所以"跑完没有"只能靠轮询进度来判定。用轮询而不是 SSE：进度本身每 100 个知识块
// 才更新一次，2 秒一次足够，而且刷新页面后能立刻接上同一条进度。

// 整库重建在 pendingIndexJob 里的标记值；其它值都是"某一本"的书名（见 finishIndexJob）
const INDEX_JOB_LABEL = '知识库'

const INDEX_PROGRESS_POLL_MS = 2000
// 连续这么多次看不到"正在跑"才认定结束。两次可能读到"其实还在跑"的 idle：点击后
// 首个进度帧可能赶在启动请求之前落下，结束时落后一拍的那帧也还会说 running。
// 只看到一次 idle 就收工，会把这两种情况都误判成"已经结束"。
const INDEX_PROGRESS_IDLE_STREAK = 3

const currentIndexingDocumentId = computed(() => {
  const progress = indexProgress.value
  return progress && progress.running ? progress.current_document_id : null
})

const showIndexProgress = computed(() => {
  const progress = indexProgress.value
  return Boolean(progress && (progress.running || progress.stalled))
})

const indexProgressTitle = computed(() => {
  const progress = indexProgress.value
  if (!progress) return ''
  if (progress.stalled) return '索引可能已中断'
  if (progress.stage === 'preparing') return '正在准备向量模型'
  return '正在建立索引'
})

const indexProgressHint = computed(() => {
  const progress = indexProgress.value
  if (!progress) return ''
  if (progress.stalled) {
    return '已经超过 10 分钟没有进度更新，可能是服务重启或任务被中断。重新点击"建立索引"即可重试。'
  }
  if (progress.stage === 'preparing') {
    return '首次运行需要先下载中文向量模型（几十 MB），这一段时间没有进度变化是正常的，请不要重复提交。'
  }
  return '每写入 100 个知识块更新一次进度；完成后会自动刷新文档状态。'
})

const currentDocumentLabel = computed(() => {
  const progress = indexProgress.value
  if (!progress) return ''
  if (!progress.current_document_title) {
    return progress.running ? '正在准备…' : '—'
  }
  // 优先用后端给出的"本次运行"口径：增量索引只处理少数几本，整库口径的
  // ready+1 会显示成"第 11 / 11 本"这种和实际不符的位置。
  const total = progress.run_documents || progress.total_documents
  const position = progress.current_document_position
    || Math.min(progress.ready_documents + 1, progress.total_documents)
  return `${progress.current_document_title}（第 ${position} / ${total} 本）`
})

async function refreshIndexProgress() {
  try {
    const response = await fetchKnowledgeIndexProgress()
    indexProgress.value = response.data || null
  } catch (error) {
    // 进度查询失败不该打断索引本身，也不该把整个页面变成错误态：
    // 保留上一帧，界面继续显示它最后知道的进度。
    void error
  }
  return indexProgress.value
}

function stopIndexProgressPolling() {
  if (progressTimer === null) return
  window.clearInterval(progressTimer)
  progressTimer = null
  idleStreak = 0
}

function startIndexProgressPolling() {
  if (progressTimer !== null) return
  idleStreak = 0
  void refreshIndexProgress()
  progressTimer = window.setInterval(async () => {
    const progress = await refreshIndexProgress()
    if (progress && progress.running) {
      idleStreak = 0
      return
    }
    idleStreak += 1
    if (idleStreak < INDEX_PROGRESS_IDLE_STREAK) return
    // 结束（或被判定中断）：停止轮询并刷新一次列表，让状态和块数落到位。
    // 刷新页面时正好有重建在跑的情况下，正是靠这里把最终结果拉回来。
    stopIndexProgressPolling()
    await finishIndexJob(progress)
  }, INDEX_PROGRESS_POLL_MS)
}

/**
 * 一次重建结束（或被判定中断）后的收尾：刷新列表，并把结果告诉教师。
 *
 * 结果只能在这里说：启动接口早已返回，成功与失败都是后来才发生的——失败的证据是
 * 文档被标成 failed（后端在重建失败时统一落库），不是某个 HTTP 状态码。
 */
async function finishIndexJob(progress) {
  await loadDocuments()
  const label = pendingIndexJob.value
  pendingIndexJob.value = null
  indexing.value = false
  documents.value.forEach(document => {
    document.indexing = false
  })
  if (!label) return

  if (progress?.stalled) {
    ElMessage.warning('索引看起来已经中断，重新点击“建立索引”即可重试')
    return
  }
  const failed = documents.value.filter(document => document.index_status === 'failed')
  if (failed.length) {
    pageError.value = `索引失败：${failed[0].title} 未能完成向量化，请重试`
    return
  }
  if (!progress?.total_documents) {
    // 一本都没启用：后台确实重建了一个空索引，但说"已建立"只会让人以为做了很多事
    ElMessage.info('没有已启用的文档，无需建立索引')
    return
  }
  ElMessage.success(label === INDEX_JOB_LABEL ? '知识库索引已建立' : `“${label}”已完成索引`)
}

onMounted(async () => {
  await loadDocuments()
  // 页面刷新时可能正好有一次重建在跑（例如在另一个标签页触发的），要能接着显示
  const progress = await refreshIndexProgress()
  if (progress && (progress.running || progress.stalled)) startIndexProgressPolling()
})

onUnmounted(stopIndexProgressPolling)

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
  // 先开始轮询再发请求：重建在服务端后台跑，进度只能从旁边看。
  startIndexProgressPolling()
  try {
    const response = await rebuildKnowledgeIndex()
    if (response.data?.work_pending === false) {
      // 后端确认没有任何文档需要处理：没有后台任务在跑。不停轮询的话，接下来几次
      // "空闲"探测会走 finishIndexJob，把这次空操作报成"知识库索引已建立"，
      // 教师会以为真的重建了一遍还白等一段时间。
      pendingIndexJob.value = null
      stopIndexProgressPolling()
      indexing.value = false
      await loadDocuments()
      ElMessage.info('索引已是最新，无需重新建立')
      return
    }
    // 接口返回只说明"已启动"：结束与成功/失败都由 finishIndexJob 在轮询判定结束时给出，
    // 所以这里不能停轮询、也不能说"已建立"。
    pendingIndexJob.value = INDEX_JOB_LABEL
    // 启动请求慢到超过"连续空闲"判定时，轮询已经收工了；这时要把它重新支起来，
    // 否则按钮会一直停在"建立中"，直到刷新页面。
    if (progressTimer === null) startIndexProgressPolling()
    if (response.data?.started === false) {
      ElMessage.info('这个知识库正在建立索引，进度见上方进度条')
    } else {
      ElMessage.success('已开始建立索引，进度见上方进度条')
    }
  } catch (error) {
    pendingIndexJob.value = null
    indexing.value = false
    pageError.value = apiErrorMessage(error, '索引启动失败，请检查模型和网络后重试')
    // 启动失败不代表后台没在跑（例如上一次的请求还在继续），所以只有确认没有在跑的
    // 重建时才收工，否则保留轮询把真实进展显示出来。
    if (!indexProgress.value?.running) {
      stopIndexProgressPolling()
      await loadDocuments()
    }
  }
}

async function indexDocument(document) {
  if (document.indexing || !document.enabled) return
  document.indexing = true
  // 单本文档的"重新索引"只重做这一本，后端会跳过其它已建好的文档；进度显示与整体
  // 索引共用同一条
  startIndexProgressPolling()
  try {
    const response = await indexKnowledgeDocument(document.document_id)
    if (!pendingIndexJob.value) pendingIndexJob.value = document.title
    // 同 rebuildIndex：启动请求期间轮询可能已经按"空闲"收工
    if (progressTimer === null) startIndexProgressPolling()
    if (response.data?.started === false) {
      ElMessage.info('知识库正在建立索引，进度见上方进度条')
    } else {
      ElMessage.success('已开始建立索引，进度见上方进度条')
    }
  } catch (error) {
    document.indexing = false
    const message = apiErrorMessage(error, '文档索引启动失败，请重试')
    pageError.value = message
    if (!indexProgress.value?.running) {
      stopIndexProgressPolling()
      await loadDocuments()
    }
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

/* 进度面板：事实项允许换行成多行，窄屏下也不会挤成一行 */
.progress-facts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-5);
  margin-top: var(--space-3);
  color: var(--text-secondary);
  font-size: var(--text-sm);
}

.progress-facts strong {
  color: var(--text-primary);
  font-weight: 600;
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

/* ── 库概览：专业数据库的规模与体检 ───────────────────────────── */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--space-3);
}

.overview-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
}

.overview-value {
  color: var(--text-primary);
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.overview-label {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.overview-alert {
  margin-top: var(--space-3);
}

/* 抽字率：低于阈值用警示色，一眼标出"这本是扫描件，导进来也检索不到" */
.coverage-tag {
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--bg-surface-sunken);
  color: var(--text-tertiary);
}

.coverage-tag.is-low {
  background: var(--warning-50);
  color: var(--warning-600);
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
