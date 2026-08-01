<template>
  <div class="chat-input-bar">
    <el-input
      v-model="text"
      type="textarea"
      :rows="2"
      placeholder="输入你的教学想法，或告诉 AI 你想教什么……"
      resize="none"
      :disabled="disabled"
      @keydown.enter.exact="handleEnter"
    />
    <div class="input-actions">
      <el-tooltip content="语音输入" placement="top" v-if="showVoice">
        <el-button
          class="btn-voice"
          circle
          :type="recording ? 'danger' : 'default'"
          @click="$emit('toggle-voice')"
          :disabled="disabled"
        >
          <el-icon :size="18"><Microphone v-if="!recording" /><Loading v-else /></el-icon>
        </el-button>
      </el-tooltip>
      <el-button
        type="primary"
        :disabled="!text.trim() || disabled"
        :loading="sending"
        @click="handleSend"
      >
        <el-icon style="margin-right: 6px"><Promotion /></el-icon>
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Microphone, Loading, Promotion } from '@element-plus/icons-vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  sending: { type: Boolean, default: false },
  recording: { type: Boolean, default: false },
  showVoice: { type: Boolean, default: true },
})

const emit = defineEmits(['send', 'toggle-voice'])

const text = ref('')

function handleSend() {
  const trimmed = text.value.trim()
  if (!trimmed || props.disabled || props.sending) return
  emit('send', trimmed)
  text.value = ''
}

function handleEnter(e) {
  if (!e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// 暴露清空方法
defineExpose({ clear: () => { text.value = '' } })
</script>

<style scoped>
.chat-input-bar {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.chat-input-bar :deep(.el-textarea__inner) {
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
}

.input-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex: 0 0 auto;
}

.btn-voice {
  width: 36px;
  height: 36px;
}
</style>
