<template>
  <div class="question-card">
    <div class="qc-header">
      <el-icon :size="18" color="#1463ff"><QuestionFilled /></el-icon>
      <span>TeachMate 想了解更多</span>
    </div>

    <p class="qc-prompt">{{ data.prompt || '请选择或输入你的想法：' }}</p>

    <!-- 预设选项 -->
    <div v-if="data.options?.length" class="qc-options">
      <el-button
        v-for="(opt, idx) in data.options"
        :key="idx"
        :type="selectedOption === idx ? 'primary' : 'default'"
        size="default"
        @click="selectOption(idx)"
      >
        {{ opt }}
      </el-button>
    </div>

    <!-- 自由输入 -->
    <div class="qc-free-input">
      <el-input
        v-model="freeText"
        type="textarea"
        :rows="2"
        placeholder="也可以输入你自己的回答……"
        resize="none"
      />
    </div>

    <!-- 操作区 -->
    <div class="qc-actions">
      <el-button size="default" @click="$emit('skip')" text>跳过</el-button>
      <el-button
        type="primary"
        size="default"
        :disabled="!canSubmit"
        @click="handleSubmit"
      >
        提交
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps({
  data: { type: Object, default: () => ({ prompt: '', options: [] }) },
})

const emit = defineEmits(['submit', 'skip'])

const selectedOption = ref(-1)
const freeText = ref('')

const canSubmit = computed(() => selectedOption.value >= 0 || freeText.value.trim())

function selectOption(idx) {
  selectedOption.value = selectedOption.value === idx ? -1 : idx
}

function handleSubmit() {
  const chosen = props.data.options?.[selectedOption.value]
  emit('submit', {
    selected: chosen || null,
    freeText: freeText.value.trim() || null,
  })
}
</script>

<style scoped>
.question-card {
  border: 1px solid #d0ddf7;
  border-radius: 10px;
  background: #f8faff;
  padding: 18px;
  margin: 8px 0;
}

.qc-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  color: #1463ff;
  margin-bottom: 10px;
}

.qc-prompt {
  margin: 0 0 14px;
  color: #1e293b;
  font-size: 14px;
  line-height: 1.6;
}

.qc-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.qc-free-input {
  margin-bottom: 14px;
}

.qc-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
