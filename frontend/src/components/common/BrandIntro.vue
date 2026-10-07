<template>
  <Transition name="intro" appear @after-leave="$emit('closed')">
    <div
      v-if="visible"
      class="intro"
      :class="{ 'is-fallback': mode === 'fallback' }"
      role="dialog"
      aria-modal="true"
      aria-label="品牌开场"
    >
      <!-- 主路径：视频。播放成功时它就是整个开场。 -->
      <video
        v-show="mode === 'video'"
        ref="videoRef"
        class="intro-video"
        :src="videoSrc"
        autoplay
        muted
        playsinline
        preload="auto"
        @timeupdate="handleTimeUpdate"
        @ended="dismiss"
        @error="useFallback"
      />

      <!-- 兜底：自动播放被浏览器拦下时，用静态渐变 + 字标，绝不留黑屏 -->
      <div v-if="mode === 'fallback'" class="intro-fallback surface-mesh" aria-hidden="true">
        <img class="fallback-lockup" :src="logoLockup" alt="" />
      </div>

      <div class="intro-scrim" aria-hidden="true" />

      <div class="intro-top">
        <button ref="primaryRef" class="intro-skip" type="button" @click="dismiss">
          {{ mode === 'fallback' ? '进入 easy-teach' : '跳过' }}
          <el-icon><Right /></el-icon>
        </button>
      </div>

      <div class="intro-bottom">
        <div class="intro-bottom-row">
          <p class="intro-caption">多模态 AI 教学智能体 · 从需求共创到可直接上课的教案</p>

          <!-- 声音开关：浏览器普遍禁止"带声自动播放"，所以先静音起播，
               再把这个按钮交给用户——点击是真实手势，解除静音一定被允许。 -->
          <button
            v-if="mode === 'video'"
            class="intro-sound"
            type="button"
            :aria-pressed="soundOn"
            :aria-label="soundOn ? '关闭声音' : '开启声音'"
            @click="toggleSound"
          >
            <svg class="sound-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 9.5v5h3.2L12 18.5v-13L7.2 9.5H4z" />
              <path v-if="soundOn" d="M15.5 8.6a4.5 4.5 0 0 1 0 6.8" />
              <path v-if="soundOn" d="M18 6.2a8 8 0 0 1 0 11.6" />
              <path v-else d="M16 9.5l5 5M21 9.5l-5 5" />
            </svg>
            {{ soundOn ? '声音已开' : '开启声音' }}
          </button>
        </div>

        <div v-if="mode === 'video'" class="intro-progress" aria-hidden="true">
          <span class="intro-progress-fill" :style="{ width: progressPercent }" />
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Right } from '@element-plus/icons-vue'
import logoLockup from '@/assets/brand/logo-lockup.png'

const emit = defineEmits(['closed'])

// 视频放在 public/ 下，用绝对路径直接引用，避免打包器处理大文件
const videoSrc = '/gemini_generated_video_d2c2908f.mp4'
// 视频 10 秒；给一点余量，播不完也不至于让用户干等
const SAFETY_TIMEOUT_MS = 14000

const visible = ref(true)
const mode = ref('video')
const videoRef = ref(null)
const primaryRef = ref(null)
const progressPercent = ref('0%')
const soundOn = ref(false)

/**
 * 起播：先尝试带声播放（源文件确实有音轨），被浏览器拦下时退回静音播放，
 * 并露出"开启声音"按钮。以前是直接写死 muted，所以永远没有声音。
 */
async function startPlayback() {
  const video = videoRef.value
  video.volume = 0.9
  video.muted = false
  try {
    await video.play()
    soundOn.value = true
    return
  } catch {
    // 无用户手势的带声播放被拦截（Chrome/Edge/Safari 默认策略），属预期行为
  }
  video.muted = true
  try {
    await video.play()
  } catch {
    useFallback()
    return
  }
  soundOn.value = false
}

/** 用户点击：这是真实手势，解除静音一定被允许。 */
function toggleSound() {
  const video = videoRef.value
  if (!video) return
  video.muted = !video.muted
  soundOn.value = !video.muted
  if (!video.muted && video.paused) video.play().catch(() => {})
}

let safetyTimer = null
let previousOverflow = ''

function handleTimeUpdate() {
  const video = videoRef.value
  if (!video || !video.duration) return
  const ratio = Math.min(1, video.currentTime / video.duration)
  progressPercent.value = `${(ratio * 100).toFixed(2)}%`
}

/** 自动播放被拦或视频异常：不关掉开场，而是换成静态版，用户点"进入"即可。 */
function useFallback() {
  if (mode.value === 'fallback') return
  clearSafetyTimer()
  mode.value = 'fallback'
}

function clearSafetyTimer() {
  if (safetyTimer !== null) {
    window.clearTimeout(safetyTimer)
    safetyTimer = null
  }
}

function dismiss() {
  if (!visible.value) return
  clearSafetyTimer()
  visible.value = false
}

function handleKeydown(event) {
  if (event.key === 'Escape') dismiss()
}

onMounted(async () => {
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', handleKeydown)
  primaryRef.value?.focus()

  safetyTimer = window.setTimeout(dismiss, SAFETY_TIMEOUT_MS)

  const video = videoRef.value
  if (!video) {
    useFallback()
    return
  }
  await startPlayback()
})

onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  window.removeEventListener('keydown', handleKeydown)
  clearSafetyTimer()
  // 停止下载与播放，避免离开后仍在后台占用带宽
  const video = videoRef.value
  if (video) {
    video.pause()
    video.removeAttribute('src')
    video.load()
  }
})
</script>

<style scoped>
.intro {
  position: fixed;
  inset: 0;
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  background: var(--surface-immersive);
  overflow: hidden;
  cursor: default;
}

.intro-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* 源文件是 720p，全屏铺满必然被放大（1920 宽屏 1.5 倍、HiDPI 笔记本 2 倍以上），
     轻微提对比能找回一点"锐度感"。真正治本还是换 1080p/2K 的源文件。 */
  filter: contrast(1.06) saturate(1.06);
}

/* 声音开关：与"跳过"同款的玻璃质感小胶囊 */
.intro-sound {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: var(--radius-full);
  background: rgba(11, 17, 32, 0.42);
  color: rgba(255, 255, 255, 0.92);
  font-size: var(--text-sm);
  cursor: pointer;
  backdrop-filter: blur(8px);
  transition: background-color var(--duration-base) var(--ease-standard),
    border-color var(--duration-base) var(--ease-standard);
}

.intro-sound:hover {
  background: rgba(11, 17, 32, 0.58);
  border-color: rgba(255, 255, 255, 0.4);
}

.sound-icon {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* 兜底背景：静态 Mesh 渐变 + 字标，观感上仍是"设计过的开场" */
.intro-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background-color: var(--surface-immersive);
  /* 暗底上的渐变要压低亮度，字标才立得住 */
  filter: brightness(0.55) saturate(1.1);
}

.fallback-lockup {
  width: min(320px, 60vw);
  height: auto;
  filter: brightness(1.9);
}

/* 只留很轻的四周压暗做纵深。
   视频画面本身可能是浅色的（实测这一版就是白底），重压暗会变成一层脏灰，
   所以"文字可读"交给下面的毛玻璃胶囊来保证，而不是靠整屏压黑。 */
.intro-scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    to bottom,
    rgba(11, 17, 32, 0.24) 0%,
    rgba(11, 17, 32, 0) 22%,
    rgba(11, 17, 32, 0) 70%,
    rgba(11, 17, 32, 0.32) 100%
  );
}

.intro-top,
.intro-bottom {
  position: relative;
  padding: var(--space-6) var(--space-8);
}

.intro-top {
  display: flex;
  justify-content: flex-end;
}

.intro-skip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 40px;
  padding: 0 var(--space-5);
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.12);
  color: var(--surface-immersive-text);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  backdrop-filter: blur(6px);
  transition: background-color var(--duration-base) var(--ease-standard),
    border-color var(--duration-base) var(--ease-standard),
    transform var(--duration-fast) var(--ease-standard);
}

.intro-skip:hover {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.4);
  transform: translateY(-1px);
}

.intro-skip:focus-visible {
  outline: none;
  box-shadow: var(--ring-inverse);
}

.intro-bottom {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.intro-bottom-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

/* 文案装在毛玻璃胶囊里：不管视频这帧是白底还是深底，都读得清 */
.intro-caption {
  align-self: flex-start;
  padding: var(--space-2) var(--space-4);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: var(--radius-pill);
  background: rgba(11, 17, 32, 0.42);
  color: rgba(255, 255, 255, 0.92);
  font-size: var(--text-sm);
  letter-spacing: 0.01em;
  backdrop-filter: blur(8px);
}

.intro-progress {
  height: 2px;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.2);
  overflow: hidden;
}

.intro-progress-fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  background: var(--gradient-brand);
}

/* ── 转场：淡出 + 轻微放大，避免"突然消失" ───────────────── */
.intro-leave-active {
  transition: opacity 320ms var(--ease-standard);
}

.intro-leave-active .intro-video {
  transition: transform 320ms var(--ease-standard);
}

.intro-leave-to {
  opacity: 0;
}

.intro-leave-to .intro-video {
  transform: scale(1.03);
}

@media (max-width: 768px) {
  .intro-top,
  .intro-bottom {
    padding: var(--space-4) var(--space-5);
  }

  .intro-caption { font-size: var(--text-xs); }

  .fallback-lockup { width: min(240px, 68vw); }
}
</style>
