<template>
  <div class="stage-progress">
    <div class="stage-progress-head">
      <el-progress
        class="stage-progress-bar"
        :percentage="safePercent"
        :status="barStatus"
        :stroke-width="10"
        :show-text="false"
      />
      <div class="stage-progress-meta">
        <span class="stage-progress-label">{{ label || '准备中……' }}</span>
        <span class="stage-progress-numbers">
          <strong>{{ safePercent }}%</strong>
          <span v-if="elapsed" class="stage-progress-elapsed">已用 {{ elapsed }}</span>
        </span>
      </div>
    </div>

    <!-- 步骤清单缺省时（例如单格式导出）只显示进度条与文案 -->
    <ol v-if="steps.length" class="stage-progress-steps">
      <li v-for="step in steps" :key="step.key" class="step" :class="stepState(step)">
        <span class="step-marker" aria-hidden="true">
          <el-icon v-if="stepState(step) === 'is-done'"><Check /></el-icon>
          <el-icon v-else-if="stepState(step) === 'is-current'"><Loading /></el-icon>
          <span v-else class="step-dot"></span>
        </span>
        <span class="step-label">
          {{ stepState(step) === 'is-current' && label ? label : step.label }}
        </span>
      </li>
    </ol>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, Loading } from '@element-plus/icons-vue'

const props = defineProps({
  /** 0-100，由后端按阶段下发 */
  percent: { type: Number, default: 0 },
  /** 当前在做什么。步骤条存在时，当前步骤会优先显示这句而不是步骤名 */
  label: { type: String, default: '' },
  /** 步骤清单 [{ key, label, percent }]；缺省时组件退化为"进度条 + 一行文案" */
  steps: { type: Array, default: () => [] },
  /** 当前步骤的 key */
  current: { type: String, default: '' },
  /** 开始时刻，用来显示已用时长；不传则不显示 */
  startedAt: { type: [String, Date, Number], default: null },
  /** 已结束：进度条转绿，步骤全部标记完成 */
  done: { type: Boolean, default: false },
})

const safePercent = computed(() => Math.max(0, Math.min(100, Math.round(props.percent || 0))))
const barStatus = computed(() => (props.done || safePercent.value >= 100 ? 'success' : undefined))

// 已用时长纯前端计算：后端只给开始时刻，不需要额外的存储
const now = ref(Date.now())
let timer = null

function toTime(value) {
  if (value === null || value === undefined || value === '') return null
  const parsed = value instanceof Date ? value.getTime() : new Date(value).getTime()
  return Number.isNaN(parsed) ? null : parsed
}

function formatSeconds(total) {
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return seconds ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分`
}

const elapsed = computed(() => {
  const start = toTime(props.startedAt)
  if (start === null || props.done) return ''
  return formatSeconds(Math.max(0, Math.round((now.value - start) / 1000)))
})

function stepState(step) {
  const order = props.steps.map(item => item.key)
  const index = order.indexOf(step.key)
  const currentIndex = order.indexOf(props.current)
  if (currentIndex === -1) return props.done ? 'is-done' : 'is-pending'
  if (index < currentIndex) return 'is-done'
  if (index === currentIndex) return props.done ? 'is-done' : 'is-current'
  return 'is-pending'
}

// 只在真的需要计时的时候跑定时器，空闲页面不应该每秒唤醒一次
function syncTimer() {
  const needed = props.startedAt !== null && !props.done
  if (needed && timer === null) {
    timer = window.setInterval(() => { now.value = Date.now() }, 1000)
  } else if (!needed && timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

onMounted(syncTimer)
watch(() => [props.startedAt, props.done], syncTimer)
onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
  timer = null
})
</script>

<style scoped>
.stage-progress { display: grid; gap: 10px; }

.stage-progress-head { display: grid; gap: 6px; }

.stage-progress-bar :deep(.el-progress-bar__outer) { border-radius: 999px; }

.stage-progress-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.stage-progress-label {
  min-width: 0;
  color: #334155;
  font-size: 13px;
  font-weight: 600;
}

.stage-progress-numbers {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: none;
  font-size: 13px;
  color: #0b3fa8;
}

.stage-progress-elapsed { color: #94a3b8; font-weight: 400; }

/* 步骤条：已完成打勾、当前高亮、未开始置灰 */
.stage-progress-steps {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.step {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  font-size: 12.5px;
  line-height: 1.4;
  color: #94a3b8;
}

.step.is-done { color: #16a34a; }
.step.is-current { color: #1463ff; font-weight: 600; }

.step-marker {
  display: grid;
  place-items: center;
  font-size: 13px;
}

.step-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.45;
}
</style>
