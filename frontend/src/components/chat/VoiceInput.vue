<template>
  <div class="voice-input">
    <!-- 录音按钮：点击开始 / 点击结束 -->
    <div
      class="voice-btn"
      :class="{ recording, transcribing, disabled: transcribing }"
      role="button"
      :aria-label="label"
      tabindex="0"
      @click="toggle"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
    >
      <el-icon :size="22">
        <Microphone v-if="state === 'idle'" />
        <Loading v-else class="spin" />
      </el-icon>
      <span v-if="showLabel">{{ label }}</span>
    </div>

    <!-- 录音中的波形 + 计时 -->
    <div v-if="recording" class="voice-meta">
      <div class="voice-wave">
        <span v-for="i in 5" :key="i" class="wave-bar" :style="{ animationDelay: `${i * 0.1}s` }" />
      </div>
      <span class="voice-timer">{{ elapsedLabel }} · 点击结束</span>
    </div>

    <!-- 错误提示 -->
    <div v-if="errorMsg" class="voice-error">
      <el-alert :title="errorMsg" type="warning" :closable="true" @close="errorMsg = ''" />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Microphone, Loading } from '@element-plus/icons-vue'
import { getApiErrorMessage, transcribeAudio } from '@/api'

/** 录音上限：超过自动结束并转写，避免长时间占用麦克风。 */
const MAX_SECONDS = 60
/** 低于该时长视为误触，不发起转写请求。 */
const MIN_MILLISECONDS = 400

const props = defineProps({
  showLabel: { type: Boolean, default: false },
  sessionId: { type: String, default: null },
  /** 面板一出现就开始录音：让"点麦克风即开始说话"成立，不用再点第二次。 */
  autoStart: { type: Boolean, default: false },
})

const emit = defineEmits(['transcribed', 'recording'])

/** idle → recording → transcribing → idle */
const state = ref('idle')
const errorMsg = ref('')
const elapsedSeconds = ref(0)

const recording = computed(() => state.value === 'recording')
const transcribing = computed(() => state.value === 'transcribing')
const label = computed(() => {
  if (state.value === 'recording') return '录音中，点击结束'
  if (state.value === 'transcribing') return '正在识别…'
  return '点击开始说话'
})
const elapsedLabel = computed(() => {
  const total = elapsedSeconds.value
  const mm = String(Math.floor(total / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${mm}:${ss}`
})

let mediaRecorder = null
let mediaStream = null
let audioChunks = []
let timerId = null
let startedAt = 0

function pickMimeType() {
  // Safari 只支持 audio/mp4，Chrome/Firefox 支持 webm/opus。
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  return candidates.find(type => MediaRecorder.isTypeSupported?.(type)) || ''
}

/**
 * 后端按扩展名做白名单校验（.wav/.mp3/.m4a/.ogg/.webm/.flac），
 * 所以这里必须给出与容器匹配的文件名，不能依赖 FormData 默认的 "blob"。
 */
function filenameFor(mimeType) {
  if (mimeType.includes('mp4')) return 'voice.m4a'
  if (mimeType.includes('ogg')) return 'voice.ogg'
  return 'voice.webm'
}

function releaseStream() {
  mediaStream?.getTracks().forEach(track => track.stop())
  mediaStream = null
}

function stopTimer() {
  if (timerId !== null) {
    clearInterval(timerId)
    timerId = null
  }
}

/** 无论成功失败，都要回到 idle 并释放麦克风。 */
function reset({ keepError = false } = {}) {
  stopTimer()
  releaseStream()
  mediaRecorder = null
  audioChunks = []
  elapsedSeconds.value = 0
  state.value = 'idle'
  emit('recording', false)
  if (!keepError) errorMsg.value = ''
}

async function toggle() {
  if (state.value === 'transcribing') return
  if (state.value === 'recording') {
    stopAndTranscribe()
    return
  }
  await startRecord()
}

async function startRecord() {
  errorMsg.value = ''
  if (!navigator.mediaDevices?.getUserMedia) {
    errorMsg.value = '当前浏览器不支持录音，请改用 Chrome / Edge'
    return
  }

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (err) {
    if (err?.name === 'NotAllowedError' || err?.name === 'SecurityError') {
      errorMsg.value = '麦克风权限被拒绝，请在浏览器地址栏允许后重试'
    } else if (err?.name === 'NotFoundError') {
      errorMsg.value = '没有检测到麦克风设备'
    } else {
      errorMsg.value = '无法启动录音，请检查音频设备'
    }
    console.error('录音启动失败:', err)
    releaseStream()
    return
  }

  // 关键：先建立 recorder 再改状态，避免"点击过快 → stop 先于 start"的竞态。
  audioChunks = []
  const mimeType = pickMimeType()
  mediaRecorder = mimeType ? new MediaRecorder(mediaStream, { mimeType }) : new MediaRecorder(mediaStream)
  mediaRecorder.ondataavailable = (event) => {
    if (event.data?.size > 0) audioChunks.push(event.data)
  }
  mediaRecorder.onstop = () => {
    void finishTranscription()
  }
  mediaRecorder.onerror = (event) => {
    console.error('录音出错:', event?.error || event)
    errorMsg.value = '录音过程中出错，请重试'
  }

  mediaRecorder.start()
  startedAt = Date.now()
  elapsedSeconds.value = 0
  state.value = 'recording'
  emit('recording', true)
  timerId = setInterval(() => {
    elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
    if (elapsedSeconds.value >= MAX_SECONDS) stopAndTranscribe()
  }, 250)
}

function stopAndTranscribe() {
  if (state.value !== 'recording') return
  state.value = 'transcribing'
  stopTimer()
  if (mediaRecorder?.state === 'recording') {
    mediaRecorder.stop() // 触发 onstop → finishTranscription
  } else {
    void finishTranscription()
  }
}

async function finishTranscription() {
  const durationMs = Date.now() - startedAt
  const mimeType = mediaRecorder?.mimeType || 'audio/webm'
  const blob = new Blob(audioChunks, { type: mimeType })
  stopTimer()
  releaseStream()
  emit('recording', false)

  if (!blob.size || durationMs < MIN_MILLISECONDS) {
    reset()
    errorMsg.value = '说话时间太短，请按住节奏说完一句'
    return
  }

  state.value = 'transcribing'
  try {
    const response = await transcribeAudio(blob, props.sessionId, filenameFor(mimeType))
    const text = (response.data?.text || '').trim()
    reset()
    if (!text) {
      errorMsg.value = '没有识别到内容，请靠近麦克风再说一次'
      return
    }
    emit('transcribed', text)
  } catch (err) {
    // 让后端的具体原因（格式/体积/服务不可用）显示出来，而不是笼统的"失败"。
    const message = await getApiErrorMessage(err, '语音识别失败，请重试')
    reset({ keepError: true })
    errorMsg.value = message
    console.error('语音转录失败:', err)
  }
}

onMounted(() => {
  if (props.autoStart) void startRecord()
})

onBeforeUnmount(() => {
  stopTimer()
  if (mediaRecorder?.state === 'recording') {
    mediaRecorder.onstop = null
    mediaRecorder.stop()
  }
  releaseStream()
  emit('recording', false)
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

.voice-btn:focus-visible {
  outline: 3px solid #1463ff33;
  outline-offset: 2px;
}

.voice-btn.recording {
  border-color: #ef4444;
  background: #fef2f2;
  color: #ef4444;
  box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.12);
}

.voice-btn.transcribing {
  border-color: #1463ff;
  color: #1463ff;
  cursor: progress;
}

.voice-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.voice-timer {
  color: #ef4444;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
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

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.voice-error {
  width: 100%;
}
</style>
