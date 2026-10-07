<template>
  <div class="chat-input-bar">
    <el-input
      v-model="text"
      type="textarea"
      :autosize="{ minRows: 2, maxRows: 6 }"
      placeholder="输入你的教学想法，或告诉 AI 你想教什么……"
      resize="none"
      :disabled="disabled"
      @keydown.enter.exact="handleEnter"
    />

    <div class="input-actions">
      <el-tooltip v-if="showVoice" content="语音输入" placement="top">
        <el-button
          class="btn-voice"
          circle
          :type="recording ? 'danger' : 'default'"
          aria-label="语音输入"
          :disabled="disabled || recording"
          @click="$emit('toggle-voice')"
        >
          <el-icon><Microphone v-if="!recording" /><Loading v-else /></el-icon>
        </el-button>
      </el-tooltip>

      <el-button
        type="primary"
        :disabled="!text.trim() || disabled"
        :loading="sending"
        @click="handleSend"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Loading, Microphone } from '@element-plus/icons-vue'

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

// 暴露清空 / 追加方法（追加用于语音转写回填）
defineExpose({
  clear: () => { text.value = '' },
  appendText: (value) => {
    const addition = (value || '').trim()
    if (!addition) return
    text.value = text.value ? `${text.value}${addition}` : addition
  },
})
</script>

<style scoped>
.chat-input-bar {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.chat-input-bar :deep(.el-textarea__inner) {
  border-radius: var(--radius-lg);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
  padding: var(--space-3) var(--space-4);
}

.input-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 0 0 auto;
}

.btn-voice {
  width: 40px;
  height: 40px;
}

@media (max-width: 480px) {
  .chat-input-bar { gap: var(--space-2); }

  .btn-voice { display: none; }
}
</style>
