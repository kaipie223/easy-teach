<template>
  <el-card>
    <template #header>👁️ 课件预览</template>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="PPT 预览" name="ppt">
        <div class="preview-placeholder">
          <p>PPT 课件预览区域</p>
          <p style="color: #999;">生成完成后将在此处展示</p>
        </div>
      </el-tab-pane>

      <el-tab-pane label="教案预览" name="docx">
        <div class="preview-placeholder">
          <p>Word 教案预览区域</p>
          <p style="color: #999;">生成完成后将在此处展示</p>
        </div>
      </el-tab-pane>

      <el-tab-pane label="互动动画" name="html">
        <div class="preview-placeholder">
          <p>动画/互动游戏预览区域</p>
          <p style="color: #999;">生成完成后将在此处展示</p>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 反馈区 -->
    <div class="feedback-area">
      <el-input
        v-model="feedback"
        type="textarea"
        :rows="2"
        placeholder="对课件不满意？在此输入修改意见..."
      />
      <el-button type="warning" @click="submitFeedback" style="margin-top: 8px;">
        提交修改意见
      </el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'

const activeTab = ref('ppt')
const feedback = ref('')

function submitFeedback() {
  // TODO: 调用 feedback API
  feedback.value = ''
}
</script>

<style scoped>
.preview-placeholder {
  height: 400px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  border-radius: 8px;
}
.feedback-area { margin-top: 16px; }
</style>
