<template>
  <div class="file-uploader">
    <!-- 拖拽/点击区域 -->
    <div
      class="upload-zone"
      :class="{ 'is-dragover': dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="handleDrop"
    >
      <input
        ref="fileInput"
        type="file"
        :accept="acceptedFormats"
        multiple
        hidden
        @change="handleFileSelect"
      />
      <el-icon :size="32" color="var(--text-disabled)"><UploadFilled /></el-icon>
      <div class="upload-hint">
        <strong>拖拽文件到此处</strong>，或
        <el-button type="primary" link @click="$refs.fileInput.click()">选择文件</el-button>
        <br />
        <span class="upload-formats">支持 {{ formatLabels }} 格式</span>
      </div>
    </div>

    <!-- 教师备注 -->
    <div class="upload-note" v-if="showNote">
      <el-input
        v-model="refDescription"
        type="textarea"
        :rows="2"
        placeholder="给参考资料添加备注（可选），例如：'第三章的补充材料'"
      />
    </div>

    <!-- 文件列表 -->
    <div v-if="files.length" class="file-list">
      <div v-for="(f, idx) in files" :key="idx" class="file-item">
        <div class="file-info">
          <el-icon :size="20" :color="getFileColor(f.name)"><Document /></el-icon>
          <div class="file-detail">
            <span class="file-name">{{ f.name }}</span>
            <span class="file-size">{{ formatSize(f.size) }}</span>
          </div>
          <el-tag
            v-if="f.status === 'uploading'"
            size="small"
            type="warning"
          >
            上传中 {{ f.progress }}%
          </el-tag>
          <el-tag
            v-else-if="f.status === 'done'"
            size="small"
            type="success"
          >
            已上传
          </el-tag>
          <el-tag
            v-else-if="f.status === 'error'"
            size="small"
            type="danger"
          >
            失败
          </el-tag>
        </div>

        <!-- 进度条 -->
        <el-progress
          v-if="f.status === 'uploading'"
          :percentage="f.progress"
          :stroke-width="4"
          :show-text="false"
        />

        <!-- 操作 -->
        <div class="file-actions">
          <el-button
            v-if="f.status === 'error'"
            size="small"
            type="primary"
            text
            @click="retryUpload(idx)"
          >
            重试
          </el-button>
          <el-button
            size="small"
            type="danger"
            text
            @click="removeFile(idx)"
          >
            删除
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { UploadFilled, Document } from '@element-plus/icons-vue'
import { uploadFile } from '@/api'

const ACCEPTED = {
  'application/pdf': 'PDF',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'PPTX',
  'text/plain': 'TXT',
  'image/png': 'PNG',
  'image/jpeg': 'JPG',
}

defineProps({
  showNote: { type: Boolean, default: true },
})

const emit = defineEmits(['uploaded'])
const route = useRoute()

const acceptedFormats = Object.keys(ACCEPTED).join(',')
const formatLabels = Object.values(ACCEPTED).join('、')
const dragging = ref(false)
const refDescription = ref('')
const fileInput = ref(null)

const files = ref([]) // { name, size, progress, status, file_id }

// 拖拽
async function handleDrop(e) {
  dragging.value = false
  const dropped = [...e.dataTransfer.files]
  for (const file of dropped) {
    await startUpload(file)
  }
}

// 点击选择
async function handleFileSelect(e) {
  const selected = [...e.target.files]
  for (const file of selected) {
    await startUpload(file)
  }
  e.target.value = '' // 允许重复选同一文件
}

// 上传文件
async function startUpload(file, retryIdx = -1) {
  const sessionId = route.params.sessionId || route.query.sessionId
  if (!sessionId) {
    ElMessage.warning('请先进入一个教学会话后上传资料')
    return
  }

  // 格式校验
  if (!(file.type in ACCEPTED)) {
    ElMessage.warning(`不支持 ${file.name} 的格式，请上传 ${formatLabels}`)
    return
  }

  const fileEntry = {
    name: file.name,
    size: file.size,
    progress: 0,
    status: 'uploading',
    file_id: null,
  }

  if (retryIdx >= 0) {
    files.value[retryIdx] = fileEntry
  } else {
    files.value.push(fileEntry)
  }
  const idx = files.value.indexOf(fileEntry)

  try {
    const res = await uploadFile(file, sessionId, refDescription.value, {
      onUploadProgress: (e) => {
        if (e.total) {
          files.value[idx].progress = Math.round((e.loaded / e.total) * 100)
        }
      },
    })
    files.value[idx].status = 'done'
    files.value[idx].file_id = res.data.file_id
    emit('uploaded', res.data)
  } catch (err) {
    files.value[idx].status = 'error'
    console.error('上传失败:', err)
  }
}

function retryUpload(idx) {
  const f = files.value[idx]
  if (f) {
    // 需要保留原始 File 对象，这里简化处理
    ElMessage.info('请重新选择文件')
    files.value.splice(idx, 1)
  }
}

function removeFile(idx) {
  files.value.splice(idx, 1)
}

function getFileColor(name) {
  const ext = name.split('.').pop()?.toLowerCase()
  const colorMap = { pdf: 'var(--danger-500)', docx: 'var(--file-word)', pptx: 'var(--file-ppt)', txt: 'var(--text-tertiary)', png: 'var(--success-500)', jpg: 'var(--success-500)', jpeg: 'var(--success-500)' }
  return colorMap[ext] || 'var(--text-tertiary)'
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

defineExpose({ files, refDescription })
</script>

<style scoped>
.file-uploader {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.upload-zone {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  min-height: 100px;
  border: 2px dashed var(--border-strong);
  border-radius: 10px;
  background: var(--bg-surface-sunken);
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.upload-zone:hover,
.upload-zone.is-dragover {
  border-color: var(--border-brand);
  background: var(--brand-50);
}

.upload-hint {
  color: var(--text-secondary);
  font-size: var(--text-base);
  line-height: 1.6;
}

.upload-formats {
  color: var(--text-disabled);
  font-size: var(--text-sm);
}

.upload-note :deep(.el-textarea__inner) {
  border-radius: var(--radius-md);
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.file-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
}

.file-info {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.file-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.file-name {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--text-primary);
}

.file-size {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

.file-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-1);
}
</style>
