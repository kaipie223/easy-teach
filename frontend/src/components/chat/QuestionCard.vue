<template>
  <div class="question-card">
    <div class="qc-header">
      <span class="qc-mark" aria-hidden="true">
        <el-icon><QuestionFilled /></el-icon>
      </span>
      <span class="qc-title">{{ cardTitle }}</span>
      <span v-if="progressText" class="qc-progress">{{ progressText }}</span>
    </div>

    <p v-if="shownPrompt" class="qc-prompt">{{ shownPrompt }}</p>

    <!-- AI 给出的可选答案：点一下就作为回答发送；
         自由回答直接用页面下方的聊天输入框，这里不放第二个输入框。
         开场邀请卡没有候选项 —— 那是邀请，不是选择题。 -->
    <div v-if="data.options?.length" class="qc-options">
      <el-button
        v-for="(opt, idx) in data.options"
        :key="idx"
        class="qc-option"
        @click="choose(opt)"
      >
        {{ opt }}
      </el-button>
    </div>

    <p v-if="data.invite" class="qc-hint">
      一句话里带上你要的信息也可以，我少问几轮；缺的部分我再逐项问。
    </p>

    <div v-else-if="!data.refine" class="qc-actions">
      <!-- 跳过的后果写在按钮上：以前只写"跳过这项"，点完界面上什么交代都没有。
           取舍类的问题（refine）没有"跳过"一说——它不是必须回答的表单项。 -->
      <el-button text size="small" @click="skip">这项先跳过（按默认处理）</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps({
  data: { type: Object, default: () => ({ prompt: '', options: [] }) },
  /**
   * 追问文案。气泡里已经逐字展示过同一句时，父组件传空串把它隐藏。
   * 用 null 表示"未指定"，此时退回 data.prompt。
   */
  prompt: { type: String, default: null },
})

const emit = defineEmits(['submit', 'skip'])

// null 才回退到 data.prompt：空串是明确的"这句话已经显示过了，不用再显示一次"
const shownPrompt = computed(
  () => props.prompt ?? props.data.prompt ?? '请选择或输入你的想法：',
)

/**
 * 三种卡片：
 * - invite：新会话开场，邀请老师自由描述；
 * - refine：核心信息齐了，但模型还有一处影响设计的取舍要问（例如"重心放在哪一侧"）；
 * - 默认：逐项确认核心字段。
 */
const cardTitle = computed(() => {
  if (props.data?.invite) return '先聊聊这节课'
  if (props.data?.refine) return '还想确认一点'
  return '还需要你确认一下'
})

/**
 * 进度文案：后端随事件下发待确认字段与总数，卡片显示与需求单同一口径的
 * "已确认 x/y · 还剩 z 项"。刻意不写"第 N 项"——模型可能一次就填掉大半，
 * 于是第 2 个核心字段也会变成"第 10 项"（真实跑出来过），反而误导。
 * 开场邀请卡没有具体字段，不显示进度。
 */
const progressText = computed(() => {
  if (props.data?.invite) return ''
  const total = Number(props.data?.core_total || 0)
  const pending = props.data?.pending_fields
  if (!total || !Array.isArray(pending) || !pending.length) return ''
  const confirmed = Math.max(0, total - pending.length)
  return `已确认 ${confirmed}/${total} · 还剩 ${pending.length} 项`
})

function choose(option) {
  emit('submit', { selected: option, freeText: null })
}

/** 跳过：把字段名一起交给后端，让它按默认处理且不再追问（见 ChatRequest.skip_field） */
function skip() {
  emit('skip', {
    field: props.data?.field || '',
    fieldLabel: props.data?.field_label || '',
  })
}
</script>

<style scoped>
.question-card {
  padding: var(--space-5);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.qc-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.qc-mark {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  background: var(--brand-50);
  color: var(--text-brand);
  font-size: var(--text-sm);
}

.qc-title {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

/* 进度靠右：一眼看到"还剩几项"，不用去数对话 */
.qc-progress {
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.qc-hint {
  margin: var(--space-3) 0 0;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

.qc-prompt {
  margin: 0 0 var(--space-4);
  color: var(--text-secondary);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
}

.qc-options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.qc-option {
  height: auto;
  min-height: 36px;
  padding: var(--space-2) var(--space-4);
  white-space: normal;
  text-align: left;
  line-height: var(--leading-snug);
}

.qc-actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 480px) {
  .qc-options { flex-direction: column; }

  .qc-option { justify-content: flex-start; }
}
</style>
