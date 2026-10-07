<template>
  <div class="confirm-panel">
    <div class="cp-header">
      <span class="cp-mark" aria-hidden="true">
        <el-icon><Select /></el-icon>
      </span>
      <span class="cp-title">确认教学信息</span>
      <el-tag size="small" type="success">AI 整理</el-tag>
    </div>

    <div class="cp-summary">
      <div v-for="(value, key) in data.fields" :key="key" class="cp-field">
        <span class="cp-label">{{ getFieldLabel(key) }}</span>
        <span class="cp-value">{{ formatFieldValue(key, value) }}</span>
      </div>
    </div>

    <div v-if="data.note" class="cp-note">
      <el-icon><InfoFilled /></el-icon>
      <span>{{ data.note }}</span>
    </div>

    <div class="cp-actions">
      <el-button @click="$emit('modify')">
        <el-icon><EditPen /></el-icon>
        修改
      </el-button>
      <el-button type="primary" @click="$emit('confirm')">
        <el-icon><CircleCheck /></el-icon>
        确认并生成
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { Select, InfoFilled, EditPen, CircleCheck } from '@element-plus/icons-vue'

defineProps({
  data: { type: Object, default: () => ({ fields: {}, note: '' }) },
})

defineEmits(['confirm', 'modify'])

const FIELD_LABELS = {
  topic: '课程主题',
  teaching_goal: '教学目标',
  audience: '授课对象',
  target_audience: '授课对象',
  duration: '课时时长',
  duration_minutes: '课时时长',
  objectives: '教学目标',
  core_knowledge: '核心知识点',
  knowledge_points: '核心知识点',
  logic_flow: '教学流程',
  teaching_focus: '教学重点',
  teaching_difficulties: '教学难点',
  focus_difficulties: '重难点',
  output_type: '输出形式',
  output_types: '输出形式',
  interaction_ideas: '互动设计',
  style: '教学风格',
  style_preference: '教学风格',
}

function getFieldLabel(key) {
  return FIELD_LABELS[key] || '补充信息'
}

const OUTPUT_TYPE_LABELS = {
  pptx: 'PPT 课件',
  docx: 'Word 教案',
  pdf: 'PDF 打印版',
  html: '互动 HTML',
}

function formatFieldValue(key, value) {
  if (value === null || value === undefined || value === '') return '未填写'
  if (key === 'output_type' || key === 'output_types') {
    const types = Array.isArray(value) ? value : String(value).split(/[、,]/)
    return types.map(item => OUTPUT_TYPE_LABELS[item.trim()] || item.trim()).filter(Boolean).join('、')
  }
  if (Array.isArray(value)) {
    return value
      .map(item => (typeof item === 'object' && item !== null ? item.title || item.name : item))
      .filter(Boolean)
      .join('、')
  }
  return String(value)
}
</script>

<style scoped>
.confirm-panel {
  padding: var(--space-5);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.cp-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.cp-mark {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  background: var(--success-50);
  color: var(--success-500);
  font-size: var(--text-sm);
}

.cp-title {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.cp-summary {
  display: grid;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.cp-field {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}

.cp-label {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
  white-space: nowrap;
}

.cp-value {
  min-width: 0;
  color: var(--text-primary);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.cp-note {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.cp-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

@media (max-width: 640px) {
  .cp-field {
    grid-template-columns: 1fr;
    gap: var(--space-1);
  }

  .cp-label { font-weight: var(--weight-medium); }

  .cp-actions > :deep(.el-button) { flex: 1; }
}
</style>
