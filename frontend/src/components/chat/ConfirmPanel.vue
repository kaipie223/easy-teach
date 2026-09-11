<template>
  <div class="confirm-panel">
    <div class="cp-header">
      <el-icon :size="18" color="#059669"><Select /></el-icon>
      <span>确认教学信息</span>
      <el-tag size="small" type="success">AI 整理</el-tag>
    </div>

    <!-- 格式化信息展示 -->
    <div class="cp-summary">
      <div v-for="(value, key) in data.fields" :key="key" class="cp-field">
        <span class="cp-label">{{ getFieldLabel(key) }}</span>
        <span class="cp-value">{{ formatFieldValue(key, value) }}</span>
      </div>
    </div>

    <!-- 补充说明 -->
    <div v-if="data.note" class="cp-note">
      <el-icon :size="14"><InfoFilled /></el-icon>
      {{ data.note }}
    </div>

    <!-- 操作 -->
    <div class="cp-actions">
      <el-button size="default" @click="$emit('modify')">
        <el-icon style="margin-right: 4px"><EditPen /></el-icon>
        修改
      </el-button>
      <el-button type="primary" size="default" @click="$emit('confirm')">
        <el-icon style="margin-right: 4px"><CircleCheck /></el-icon>
        确认生成
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
  border: 1px solid #a7f3d0;
  border-radius: 10px;
  background: #f0fdf4;
  padding: 18px;
  margin: 8px 0;
}

.cp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-weight: 700;
  color: #065f46;
}

.cp-summary {
  display: grid;
  gap: 10px;
  margin-bottom: 12px;
}

.cp-field {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.cp-label {
  color: #6b7280;
  font-size: 13px;
  line-height: 1.7;
  text-align: right;
  white-space: nowrap;
}

.cp-value {
  min-width: 0;
  color: #111827;
  font-size: 14px;
  line-height: 1.7;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.cp-note {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 14px;
  padding: 8px 12px;
  background: #ecfdf5;
  border-radius: 6px;
}

.cp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

@media (max-width: 640px) {
  .cp-field {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .cp-label {
    font-weight: 700;
    text-align: left;
  }
}
</style>
