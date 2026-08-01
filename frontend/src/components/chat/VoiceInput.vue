<template>
  <div class="voice-input">
    <!-- 录音按钮 -->
    <div
      class="voice-btn"
      :class="{ recording }"
      @mousedown="startRecord"
      @mouseup="stopRecord"
      @mouseleave="cancelRecord"
      @touchstart.prevent="startRecord"
      @touchend.prevent="stopRecord"
    >
      <el-icon :size="22">
        <Microphone v-if="!recording" />
        <Loading v-else />
      </el-icon>
      <span v-if="showLabel">{{ recording ? '松开发送' : '按住说话' }}</span>
    </div>

    <!-- 波形动画 -->
    <div v-if="recording" class="voice-wave">
      <span v-for="i in 5" :key="i" class="wave-bar" :style="{ animationDelay: `${i * 0.1}s` }" />
    </div>

    <!-- 错误提示 -->
    <div v-if="errorMsg" class="voice-error">
      <el-alert :title="errorMsg" type="warning" :closable="true" @close="errorMsg = ''" />
    </div>
  </div>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { Microphone, Loading } from '@element-plus/icons-vue'
import { transcribeAudio } from '@/api'

defineProps({
  showLabel: { type: Boolean, default: false },
})

const emit = defineEmits(['transcribed'])

const recording = ref(false)
const errorMsg = ref('')
let mediaRecorder = null
let audioChunks = []

async function startRecord() {
  errorMsg.value = ''

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaRecorder = new MediaRecorder(stream, {
      mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm',
    })
    audioChunks = []

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) {
        audioChunks.push(e.data)
      }
    }

    mediaRecorder.onstop = async () => {
      // 释放麦克风
      stream.getTracks().forEach(t => t.stop())

      if (audioChunks.length === 0) return

      const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType })
      try {
        const res = await transcribeAudio(audioBlob)
        if (res.data?.text) {
          emit('transcribed', res.data.text)
        }
      } catch (err) {
        errorMsg.value = '语音识别失败，请重试'
        console.error('语音转录失败:', err)
      }
    }

    mediaRecorder.start()
    recording.value = true
  } catch (err) {
    if (err.name === 'NotAllowedError') {
      errorMsg.value = '请允许浏览器使用麦克风'
    } else {
      errorMsg.value = '无法启动录音，请检查设备'
    }
    console.error('录音启动失败:', err)
  }
}

function stopRecord() {
  if (!recording.value || !mediaRecorder) return

  recording.value = false

  if (mediaRecorder.state === 'recording') {
    // 至少录 0.5 秒才发送
    if (audioChunks.length > 0) {
      mediaRecorder.stop()
    } else {
      mediaRecorder.stop()
    }
  }
}

function cancelRecord() {
  if (!recording.value) return
  recording.value = false

  if (mediaRecorder?.state === 'recording') {
    mediaRecorder.stop()
    // 清空录音数据，不发送
    audioChunks = []
  }
}

onBeforeUnmount(() => {
  if (mediaRecorder?.state === 'recording') {
    mediaRecorder.stop()
  }
})
</script>

<style scoped>
.voice-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.voice-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 24px;
  border: 2px solid #e4e9f2;
  border-radius: 999px;
  background: #ffffff;
  cursor: pointer;
  user-select: none;
  transition: all 0.2s ease;
  color: #475569;
  font-size: 14px;
}

.voice-btn:hover {
  border-color: #1463ff;
  color: #1463ff;
}

.voice-btn.recording {
  border-color: #ef4444;
  background: #fef2f2;
  color: #ef4444;
  box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.12);
}

.voice-wave {
  display: flex;
  align-items: center;
  gap: 3px;
  height: 24px;
}

.wave-bar {
  width: 4px;
  height: 100%;
  background: #ef4444;
  border-radius: 2px;
  animation: wave 0.6s ease-in-out infinite alternate;
}

@keyframes wave {
  0% { height: 8px; }
  100% { height: 24px; }
}

.voice-error {
  width: 100%;
}
</style>
