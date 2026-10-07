<template>
  <div class="toto" :class="[`is-${activeState}`, { 'has-message': Boolean(message) }]">
    <!--
      刻意内联 SVG，不用 <img>。
      working / welcome 的动作是 SVG **内部的 SMIL 动画**（<animate>），而 <img> 里那份
      文档的动画用 CSS 和 JS 都够不着 —— 用户开启"减少动态效果"时根本停不下来。
      内联之后可以调 pauseAnimations()，这条无障碍要求才真正满足。
      5 个素材合计 11.6 KB，内联的体积代价可以忽略。
    -->
    <span ref="artRef" class="toto-art" :style="artStyle" role="img" :aria-label="resolvedAlt" v-html="markup" />
    <p v-if="message" class="toto-message">{{ message }}</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, nextTick, ref, watch } from 'vue'

import errorArt from '../../../public/mascot/mascot-error.svg?raw'
import idleArt from '../../../public/mascot/mascot-idle.svg?raw'
import successArt from '../../../public/mascot/mascot-success.svg?raw'
import welcomeArt from '../../../public/mascot/mascot-welcome.svg?raw'
import workingArt from '../../../public/mascot/mascot-working.svg?raw'

/** 五个状态与素材一一对应，文件名与设计交付保持一致（见 HANDOFF_TO_FRONTEND.md） */
const ART = {
  idle: idleArt,
  welcome: welcomeArt,
  working: workingArt,
  success: successArt,
  error: errorArt,
}

const STATE_LABELS = {
  idle: '托托（待机）',
  welcome: '托托在打招呼',
  working: '托托正在处理',
  success: '托托表示已完成',
  error: '托托提示需要检查',
}

const props = defineProps({
  /** 只能是 idle / welcome / working / success / error */
  state: { type: String, default: 'idle' },
  /** 默认 148px；按空间可调，内部保持等比，不拉伸 */
  size: { type: [Number, String], default: 148 },
  /** 角色下方的一句话；不传则不占位 */
  message: { type: String, default: '' },
  /** 无障碍名称；不传按状态给一句中文 */
  alt: { type: String, default: '' },
})

const activeState = computed(() =>
  Object.prototype.hasOwnProperty.call(ART, props.state) ? props.state : 'idle',
)
const resolvedAlt = computed(() => props.alt || STATE_LABELS[activeState.value] || '托托')
const markup = computed(() => ART[activeState.value])

const artStyle = computed(() => {
  const size = typeof props.size === 'number' ? `${props.size}px` : props.size
  return { width: size, height: size }
})

const artRef = ref(null)
let motionQuery = null

/**
 * 停 / 恢复 SVG 内部的 SMIL 动画。
 *
 * 注意：base.css 里那条全局降噪规则（animation-duration: 0.001ms !important）只关
 * **CSS 动画**，对 SMIL 一点用都没有 —— 所以这里必须单独用 JS 处理。
 * 归零时间轴是为了让"静止"停在起始姿势，否则会停在抬手抬到一半的样子。
 */
function syncSmil() {
  const svg = artRef.value?.querySelector('svg')
  if (!svg || typeof svg.pauseAnimations !== 'function') return
  if (motionQuery?.matches) {
    svg.pauseAnimations()
    if (typeof svg.setCurrentTime === 'function') svg.setCurrentTime(0)
  } else {
    svg.unpauseAnimations()
  }
}

function onMotionChange() {
  syncSmil()
}

onMounted(async () => {
  await nextTick()
  // 顺序要紧：先把媒体查询拿到手，再同步一次。反过来写的话第一次同步读到的
  // motionQuery 还是 null，会直接走"恢复播放"分支 —— 减少动效下动画照放。
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  syncSmil()
  motionQuery.addEventListener?.('change', onMotionChange)
})

onBeforeUnmount(() => {
  motionQuery?.removeEventListener?.('change', onMotionChange)
})

// 换状态会换掉内联的 SVG，新的那份需要重新按当前偏好处理一次
watch(activeState, async () => {
  await nextTick()
  syncSmil()
})
</script>

<style scoped>
.toto {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  /* 角色只是视觉：绝不能挡住按钮、输入框和主要操作区 */
  pointer-events: none;
}

.toto-art {
  display: block;
  flex: none;
}

/*
 * 尺寸只由 size 决定：不让 SVG 自带的 width/height 参与（交接文档要求不修改
 * SVG 源文件，那就在 CSS 这一层兜住）。!important 是为了防止将来新素材自带内联尺寸。
 */
.toto-art :deep(svg) {
  display: block;
  width: 100% !important;
  height: 100% !important;
  max-width: 100%;
  max-height: 100%;
}

.toto-message {
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
  text-align: center;
}

/*
 * 三个状态的整体动效写在组件 CSS 里；working / welcome 的挥手、挠头在 SVG 内部。
 * 全局的 prefers-reduced-motion 规则（base.css）会把这里的动画一起压静，
 * 所以这里不需要再写一份媒体查询。
 */
.toto.is-idle .toto-art {
  animation: toto-float 4s ease-in-out infinite;
}

.toto.is-success .toto-art {
  animation: toto-bounce 620ms var(--ease-out) 2;
}

.toto.is-error .toto-art {
  animation: toto-tilt 1000ms ease-in-out 2;
}

/* 幅度刻意很小：角色在旁边待着，不抢正文注意力 */
@keyframes toto-float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-6px);
  }
}

@keyframes toto-bounce {
  0%,
  100% {
    transform: translateY(0) scale(1);
  }
  35% {
    transform: translateY(-10px) scale(1.02);
  }
  70% {
    transform: translateY(0) scale(0.99);
  }
}

@keyframes toto-tilt {
  0%,
  100% {
    transform: rotate(0deg);
  }
  35% {
    transform: rotate(-5deg);
  }
  70% {
    transform: rotate(-2deg);
  }
}
</style>
