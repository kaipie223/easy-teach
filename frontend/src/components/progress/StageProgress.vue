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
.stage-progress { display: grid; gap: var(--space-3); }

.stage-progress-head { display: grid; gap: var(--space-2); }

.stage-progress-bar :deep(.el-progress-bar__outer) { border-radius: var(--radius-pill); }

.stage-progress-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.stage-progress-label {
  min-width: 0;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.stage-progress-numbers {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex: none;
  color: var(--text-brand);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.stage-progress-elapsed {
  color: var(--text-tertiary);
  font-weight: var(--weight-regular);
}

/* 步骤条：已完成打勾、当前高亮、未开始置灰 */
.stage-progress-steps {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.step {
  display: grid;
  grid-template-columns: var(--space-5) minmax(0, 1fr);
  gap: var(--space-2);
  align-items: center;
  color: var(--text-disabled);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.step.is-done { color: var(--success-500); }

.step.is-current {
  color: var(--text-brand);
  font-weight: var(--weight-semibold);
}

.step-marker {
  display: grid;
  place-items: center;
  font-size: var(--text-sm);
}

.step-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.45;
}
</style>
