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

    <!-- 录音中的波形 + 计时 + 识别状态 -->
    <div v-if="recording" class="voice-meta">
      <div class="voice-wave">
        <span v-for="i in 5" :key="i" class="wave-bar" :style="{ animationDelay: `${i * 0.1}s` }" />
      </div>
      <span class="voice-timer">{{ elapsedLabel }} · 点击结束</span>
      <span v-if="pendingUploads" class="voice-pending">识别中…</span>
    </div>

    <!-- 实时转写：边说边出（浅色=临时结果），停止后整段交给输入框 -->
    <div
      v-if="recording || transcribing || combinedText || partialText"
      ref="transcriptRef"
      class="voice-transcript"
      :class="{ 'is-empty': !combinedText && !partialText }"
      aria-live="polite"
    >
      <template v-if="combinedText || partialText">
        <span v-if="combinedText">{{ combinedText }}</span>
        <span v-if="partialText" class="voice-partial">{{ partialText }}</span>
      </template>
      <template v-else>{{ transcriptHint }}</template>
    </div>

    <!-- 错误提示 -->
    <div v-if="errorMsg" class="voice-error">
      <el-alert :title="errorMsg" type="warning" :closable="true" @close="errorMsg = ''" />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Microphone, Loading } from '@element-plus/icons-vue'
import { getApiErrorMessage, transcribeAudio } from '@/api'

/** 录音上限：超过自动结束，避免长时间占用麦克风。 */
const MAX_SECONDS = 60
/** 低于该时长视为误触，不发起转写请求。 */
const MIN_MILLISECONDS = 400
/** 音量分析节拍：10Hz，足够分辨 0.5 秒级别的停顿。 */
const ANALYSIS_INTERVAL_MS = 100
/** 时域采样窗：1024 点 ≈ 21ms@48k，算 RMS 够用。 */
const FFT_SIZE = 1024
/** 底噪样本数：约 6 秒，跟随房间底噪而不是某句话。 */
const RMS_HISTORY_SIZE = 60
/** 人声阈值 = 近段底噪的第 10 百分位 × 系数，再夹在上下限之间。 */
const SPEECH_FACTOR = 2
const THRESHOLD_MIN = 0.006
const THRESHOLD_MAX = 0.06
/** 段至少这么长才允许在停顿处切段，避免把句子切碎。 */
const MIN_SEGMENT_MS = 1200
/** 判定"说过话"与"停下来了"的最小时间。 */
const MIN_SPEECH_MS = 400
const TRAILING_SILENCE_MS = 500
/** 连续说话时的硬切上限：限制单个请求的延迟与体积（~24KB opus）。 */
const MAX_SEGMENT_MS = 8000
/** 录音中滚动转写当前段：让文字"边说边出"，而不是等点结束才识别。 */
const RECORDER_TIMESLICE_MS = 500
const PARTIAL_INTERVAL_MS = 2000
const PARTIAL_MIN_AUDIO_MS = 1200
const PARTIAL_MIN_SPEECH_MS = 300
/** 有音量分析但一直没判到人声时，靠时长兜底试一把（阈值偏保守也能出字）。 */
const PARTIAL_FALLBACK_MS = 2500
/** 段内峰值音量超过这个绝对值就算"有真实人声"，防止阈值偏保守时误丢整段。 */
const PEAK_MIN_RMS = 0.015
/** 人声短于该值的段不上传：whisper 对静音会幻觉出字幕。 */
const DISCARD_SPEECH_MS = 300
/** onstop 不触发 / 队列迟迟不排空时的保险丝。 */
const FINALIZE_TIMEOUT_MS = 2000
const DRAIN_TIMEOUT_MS = 20000

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
/** 已识别的段：{ index, text }；按 index 排序合并后就是最终文本。 */
const recognized = ref([])
/** 仍在队列里（含正在请求）的段数，用于"识别中…"提示。 */
const pendingUploads = ref(0)
/** 当前段的临时滚动结果：只是"边说边看"，段封口后以正式结果为准。 */
const partial = ref(null)
const transcriptRef = ref(null)

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
/** 面板显示的文字与最终发出的文字是同一份：显示即所发。 */
const combinedText = computed(() =>
  [...recognized.value]
    .sort((a, b) => a.index - b.index)
    .map(item => item.text)
    .join('')
)
const transcriptHint = computed(() =>
  recording.value ? '正在聆听，请自然说话…' : '正在识别…'
)
/** 临时转写的展示文本：颜色更浅，和已定稿文字区分。 */
const partialText = computed(() => partial.value?.text || '')

// 新识别出一段就滚到底，长句不用手动拉
watch([combinedText, partialText], () => {
  nextTick(() => {
    const el = transcriptRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
})

let mediaStream = null
let audioCtx = null
let analyser = null
let analysisBuf = null
let analysisAvailable = false
let rmsHistory = []
let tickTimerId = null
let lastTickAt = 0
let startedAt = 0
let activeSegment = null
let segmentIndex = 0
/** 会话令牌：新一轮录音后，旧会话的转写结果一律丢弃。 */
let sessionSeq = 0
let uploadChain = Promise.resolve()
let firstError = ''
let disposed = false

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

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

/** 音量分析是否真的在跑；不在就退化为纯 8 秒硬切。 */
function vadActive() {
  return analysisAvailable && audioCtx?.state === 'running'
}

// ── 音量分析（静音切段的依据） ──────────────────────

async function startAnalysis() {
  rmsHistory = []
  analysisAvailable = false
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!AudioCtx || !mediaStream) return
    audioCtx = new AudioCtx()
    if (audioCtx.state === 'suspended') await audioCtx.resume()
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = FFT_SIZE
    if (typeof analyser.getFloatTimeDomainData !== 'function') {
      throw new Error('getFloatTimeDomainData 不可用')
    }
    analysisBuf = new Float32Array(analyser.fftSize)
    // 只接分析节点、不接 destination，避免把麦克风声音放出来（啸叫）
    audioCtx.createMediaStreamSource(mediaStream).connect(analyser)
    analysisAvailable = audioCtx.state === 'running'
    if (!analysisAvailable) console.warn('音量分析未处于运行状态，改用固定分段')
  } catch (err) {
    console.warn('音量分析不可用，改用固定分段:', err)
    analysisAvailable = false
    stopAnalysis()
  }
}

function stopAnalysis() {
  if (tickTimerId !== null) {
    clearInterval(tickTimerId)
    tickTimerId = null
  }
  if (audioCtx && audioCtx.state !== 'closed') {
    audioCtx.close().catch(() => {})
  }
  audioCtx = null
  analyser = null
  analysisBuf = null
  rmsHistory = []
}

/** 读一次音量，返回"这一拍像不像人声"。 */
function sampleSpeech(seg) {
  try {
    analyser.getFloatTimeDomainData(analysisBuf)
  } catch (err) {
    console.warn('音量分析失效，改用固定分段:', err)
    analysisAvailable = false
    return true
  }
  let sum = 0
  for (let i = 0; i < analysisBuf.length; i += 1) {
    sum += analysisBuf[i] * analysisBuf[i]
  }
  const rms = Math.sqrt(sum / analysisBuf.length)
  if (rms > seg.peakRms) seg.peakRms = rms
  rmsHistory.push(rms)
  if (rmsHistory.length > RMS_HISTORY_SIZE) rmsHistory.shift()
  const sorted = [...rmsHistory].sort((a, b) => a - b)
  const p10 = sorted[Math.floor(sorted.length * 0.1)]
  const threshold = Math.min(THRESHOLD_MAX, Math.max(THRESHOLD_MIN, p10 * SPEECH_FACTOR))
  return rms >= threshold
}

// ── 切段：每段一个 MediaRecorder，stop() 即得到可独立解码的文件 ──

function startSegment() {
  if (state.value !== 'recording' || disposed || !mediaStream) return
  const mimeType = pickMimeType()
  let recorder
  try {
    recorder = mimeType ? new MediaRecorder(mediaStream, { mimeType }) : new MediaRecorder(mediaStream)
  } catch (err) {
    console.error('无法创建录音器:', err)
    if (!firstError) firstError = '无法启动录音，请检查音频设备'
    void stopRecording()
    return
  }

  const seg = {
    index: segmentIndex,
    mimeType,
    recorder,
    chunks: [],
    startedAt: Date.now(),
    speechMs: 0,
    silenceMs: 0,
    peakRms: 0,
    lastPartialAt: 0,
    partialInFlight: false,
    finalized: false,
    discard: false,
  }
  segmentIndex += 1
  let settle
  seg.settled = new Promise(resolve => { settle = resolve })
  seg.finalize = () => {
    // 幂等：stop() 触发的 onstop 与兜底路径只会生效一次
    if (seg.finalized) return
    seg.finalized = true
    seg.mimeType = recorder.mimeType || seg.mimeType || 'audio/webm'
    const blob = seg.chunks.length ? new Blob(seg.chunks, { type: seg.mimeType }) : null
    seg.chunks = []
    settle()
    // 人声过短的段不上传（whisper 对静音会幻觉出字幕）：音量分析说有真声音、
    // 峰值音量够大、或压根没有分析数据，三者居其一才上传
    const worthUploading = blob?.size && !disposed && !seg.discard
      && (!vadActive()
        || seg.speechMs >= DISCARD_SPEECH_MS
        || seg.peakRms >= PEAK_MIN_RMS)
    if (worthUploading) {
      enqueueTranscribe(blob, seg, { partial: false })
    } else if (partial.value?.index === seg.index) {
      partial.value = null
    }
  }
  recorder.ondataavailable = (event) => {
    if (event.data?.size > 0) seg.chunks.push(event.data)
  }
  recorder.onstop = () => seg.finalize()
  recorder.onerror = (event) => {
    console.error('录音出错:', event?.error || event)
    if (!firstError) firstError = '录音过程中出错，请重试'
    if (activeSegment === seg) {
      activeSegment = null
      seg.finalize()
      if (state.value === 'recording') startSegment()
    }
  }

  try {
    // timeslice：录音期间每 500ms 落一片，滚动转写用
    recorder.start(RECORDER_TIMESLICE_MS)
  } catch (err) {
    console.error('无法启动录音:', err)
    if (!firstError) firstError = '无法启动录音，请检查音频设备'
    void stopRecording()
    return
  }
  seg.mimeType = recorder.mimeType || seg.mimeType || 'audio/webm'
  activeSegment = seg
}

/** 当前段封口并立刻接上下一段（轮换点落在静音处，毫秒级接缝无感）。 */
function rotateSegment() {
  const seg = activeSegment
  if (!seg || seg.recorder.state !== 'recording') return
  activeSegment = null
  seg.recorder.stop()
  startSegment()
}

/** 网页端能对"流中途的快照"解码的容器：webm/ogg 可以，Safari 的 mp4 不行。 */
function partialAllowed(seg) {
  const mime = seg.mimeType || ''
  return mime.includes('webm') || mime.includes('ogg')
}

/** 录音中就滚动转写当前段，让文字"边说边出"；结果只作临时显示。 */
function maybeFirePartial(seg, now) {
  if (!partialAllowed(seg) || seg.partialInFlight || !seg.chunks.length) return
  const audioMs = now - seg.startedAt
  if (audioMs < PARTIAL_MIN_AUDIO_MS) return
  if (now - (seg.lastPartialAt || seg.startedAt) < PARTIAL_INTERVAL_MS) return
  // 有音量分析：人声够了就试；一直判不到人声则靠时长兜底（阈值偏保守时也能出字）
  if (vadActive() && seg.speechMs < PARTIAL_MIN_SPEECH_MS && audioMs < PARTIAL_FALLBACK_MS) return
  seg.lastPartialAt = now
  seg.partialInFlight = true
  const blob = new Blob(seg.chunks, { type: seg.mimeType })
  enqueueTranscribe(blob, seg, { partial: true })
}

/** 10Hz 节拍：计时、音量判定、轮换、60 秒上限。 */
function tick() {
  if (state.value !== 'recording') return
  const now = Date.now()
  const delta = lastTickAt ? now - lastTickAt : ANALYSIS_INTERVAL_MS
  lastTickAt = now
  elapsedSeconds.value = Math.floor((now - startedAt) / 1000)

  if (elapsedSeconds.value >= MAX_SECONDS) {
    void stopRecording()
    return
  }

  const seg = activeSegment
  if (!seg) return

  // 分析不可用时按"一直在说话"处理：只走硬切，不丢段
  const speech = vadActive() ? sampleSpeech(seg) : true
  if (speech) {
    seg.speechMs += delta
    seg.silenceMs = 0
  } else if (seg.speechMs > 0) {
    seg.silenceMs += delta
  }

  const segMs = now - seg.startedAt
  const hardCap = segMs >= MAX_SEGMENT_MS
  const naturalPause = (
    segMs >= MIN_SEGMENT_MS
    && seg.speechMs >= MIN_SPEECH_MS
    && seg.silenceMs >= TRAILING_SILENCE_MS
  )
  if (hardCap || naturalPause) rotateSegment()
  else maybeFirePartial(seg, now)
}

// ── 上传队列：串行，一次只发一段 ──────────────────────

function enqueueTranscribe(blob, seg, { partial: isPartial }) {
  const mySession = sessionSeq
  pendingUploads.value += 1
  uploadChain = uploadChain
    .catch(() => {}) // 前一段失败不阻断队列
    .then(async () => {
      const response = await transcribeAudio(blob, props.sessionId, filenameFor(seg.mimeType))
      if (disposed || mySession !== sessionSeq) return
      const text = (response.data?.text || '').trim()
      if (!text) return
      if (isPartial) {
        // 临时结果：段已封口（正式结果在路上）就丢弃，避免和定稿文字打架
        if (!seg.finalized) partial.value = { index: seg.index, text }
        return
      }
      recognized.value.push({ index: seg.index, text })
      if (partial.value?.index === seg.index) partial.value = null
    })
    .catch(async (err) => {
      if (isPartial) return // 临时结果失败无所谓，等下一次滚动或正式封口
      // 单段失败不打断本轮录音，只记住第一条错误
      if (!firstError) firstError = await getApiErrorMessage(err, '语音识别失败，请重试')
      console.error('语音转录失败:', err)
    })
    .finally(() => {
      if (isPartial) seg.partialInFlight = false
      if (mySession === sessionSeq) pendingUploads.value -= 1
    })
}

// ── 启动 / 停止 / 收尾 ──────────────────────────────

async function toggle() {
  if (state.value === 'transcribing') return
  if (state.value === 'recording') {
    void stopRecording()
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
  if (disposed) {
    // 等权限期间组件已被卸载
    releaseStream()
    return
  }

  // 新会话：重置令牌与队列，旧会话的转写结果不会再落到这一轮
  sessionSeq += 1
  uploadChain = Promise.resolve()
  recognized.value = []
  partial.value = null
  pendingUploads.value = 0
  firstError = ''
  segmentIndex = 0
  startedAt = Date.now()
  lastTickAt = 0
  elapsedSeconds.value = 0

  state.value = 'recording'
  emit('recording', true)
  await startAnalysis()
  if (state.value !== 'recording' || disposed) {
    // 起分析期间用户已经点了停止/面板已卸载：别再起段和节拍
    return
  }
  startSegment()
  tickTimerId = setInterval(tick, ANALYSIS_INTERVAL_MS)
}

async function stopRecording() {
  if (state.value !== 'recording') return
  // 先同步改状态：节拍从此不再接新段，也不会与轮换撞车
  state.value = 'transcribing'
  const tooShort = Date.now() - startedAt < MIN_MILLISECONDS
  const seg = activeSegment
  activeSegment = null
  if (seg) {
    if (tooShort) seg.discard = true // 误触：封口但不上传
    if (seg.recorder.state !== 'inactive') seg.recorder.stop()
    else seg.finalize()
    // onstop 偶发不触发时的保险丝
    await Promise.race([seg.settled, delay(FINALIZE_TIMEOUT_MS)])
  }
  releaseStream()
  if (!tooShort) {
    // 等队列排空（通常只剩最后一段）；保险丝到点就用已有文字收尾
    await Promise.race([uploadChain.catch(() => {}), delay(DRAIN_TIMEOUT_MS)])
  }
  finish(tooShort ? '说话时间太短，请按住节奏说完一句' : '')
}

function finish(forcedError = '') {
  cleanup()
  if (disposed) return
  state.value = 'idle'
  elapsedSeconds.value = 0
  // 到这里才解锁 ChatInput 的麦克风按钮：排空期间按钮保持禁用，防止中途关面板丢文字
  emit('recording', false)
  if (forcedError) {
    errorMsg.value = forcedError
    return
  }
  // 极端情况下正式结果没回来（比如最后一段上传失败），临时结果也比什么都没有强
  const text = (combinedText.value || partialText.value).trim()
  if (!text) {
    errorMsg.value = firstError || '没有识别到内容，请靠近麦克风再说一次'
    return
  }
  errorMsg.value = ''
  emit('transcribed', text)
}

/** 幂等收尾：停节拍、关 AudioContext、释放麦克风。 */
function cleanup() {
  stopAnalysis()
  releaseStream()
  activeSegment = null
}

onMounted(() => {
  if (props.autoStart) void startRecord()
})

onBeforeUnmount(() => {
  disposed = true
  const seg = activeSegment
  activeSegment = null
  if (seg) {
    if (seg.recorder.state !== 'inactive') seg.recorder.stop()
    else seg.finalize()
  }
  cleanup()
  emit('recording', false)
})
</script>

<style scoped>
.voice-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
}

.voice-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-6);
  border: 2px solid var(--border-light);
  border-radius: 999px;
  background: var(--bg-surface);
  cursor: pointer;
  user-select: none;
  transition: all 0.2s ease;
  color: var(--text-secondary);
  font-size: var(--text-base);
}

.voice-btn:hover {
  border-color: var(--border-brand);
  color: var(--text-brand);
}

/* 焦点样式交给 base.css 的全局 :focus-visible（统一光环），这里不再重复定义 */

.voice-btn.recording {
  border-color: var(--danger-500);
  background: var(--danger-50);
  color: var(--danger-500);
  box-shadow: var(--ring-danger);
}

.voice-btn.transcribing {
  border-color: var(--border-brand);
  color: var(--text-brand);
  cursor: progress;
}

.voice-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.voice-timer {
  color: var(--danger-500);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.voice-pending {
  color: var(--text-brand);
  font-size: var(--text-xs);
}

.voice-wave {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  height: 24px;
}

.wave-bar {
  width: 4px;
  height: 100%;
  background: var(--danger-500);
  border-radius: 2px;
  animation: wave 0.6s ease-in-out infinite alternate;
}

@keyframes wave {
  0% { height: 8px; }
  100% { height: 24px; }
}

/* 实时转写：说一句出一句，停止后排空再整段交给输入框 */
.voice-transcript {
  align-self: stretch;
  width: 100%;
  max-height: 160px;
  overflow-y: auto;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
  text-align: left;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.voice-transcript.is-empty {
  color: var(--text-tertiary);
}

/* 滚动转写的临时结果：颜色更浅，和已定稿文字区分 */
.voice-partial {
  color: var(--text-tertiary);
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
