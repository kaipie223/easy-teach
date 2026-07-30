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
        <span class="cp-value">{{ value }}</span>
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
  audience: '授课对象',
  duration: '课时时长',
  objectives: '教学目标',
  core_knowledge: '核心知识点',
  focus_difficulties: '重难点',
  output_type: '输出形式',
}

function getFieldLabel(key) {
  return FIELD_LABELS[key] || key
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
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
}

.cp-label {
  color: #6b7280;
  font-size: 13px;
  text-align: right;
}

.cp-value {
  color: #111827;
  font-size: 14px;
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
</style>
