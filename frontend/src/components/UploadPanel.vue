<template>
  <el-card class="upload-panel">
    <template #header>📂 参考资料</template>

    <el-upload
      drag
      multiple
      :auto-upload="false"
      :on-change="handleChange"
      accept=".pdf,.doc,.docx,.jpg,.png,.mp4"
    >
      <el-icon :size="40"><UploadFilled /></el-icon>
      <div>拖拽文件到此处，或点击上传</div>
      <template #tip>
        <div style="font-size: 12px; color: #999; margin-top: 8px;">
          支持 PDF / Word / 图片 / 视频
        </div>
      </template>
    </el-upload>

    <!-- 已上传列表 -->
    <div class="file-list" v-if="files.length">
      <el-tag
        v-for="f in files" :key="f.uid"
        closable
        @close="removeFile(f.uid)"
        style="margin: 4px;"
      >
        {{ f.name }}
      </el-tag>
    </div>

    <el-button
      type="success"
      @click="uploadAll"
      :loading="uploading"
      :disabled="!files.length"
      style="margin-top: 12px; width: 100%;"
    >
      上传并解析
    </el-button>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'

const files = ref([])
const uploading = ref(false)

function handleChange(file) {
  files.value.push(file)
}

function removeFile(uid) {
  files.value = files.value.filter(f => f.uid !== uid)
}

async function uploadAll() {
  uploading.value = true
  // TODO: 调用 upload API
  setTimeout(() => {
    uploading.value = false
    files.value = []
  }, 1000)
}
</script>

<style scoped>
.upload-panel { height: 100%; }
.file-list { margin-top: 12px; }
</style>
