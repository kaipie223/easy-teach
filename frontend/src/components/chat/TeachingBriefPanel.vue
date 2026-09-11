<template>
  <aside class="brief-panel" aria-labelledby="brief-title">
    <div class="brief-header">
      <div>
        <p class="eyebrow">TEACHING BRIEF</p>
        <h2 id="brief-title">教学需求确认单</h2>
      </div>
      <el-tag v-if="brief" size="small" :type="brief.status === 'confirmed' ? 'success' : 'warning'">
        {{ brief.status === 'confirmed' ? '已确认' : `草稿 v${brief.version}` }}
      </el-tag>
    </div>

    <el-empty v-if="!brief" description="发送一条教学想法后生成摘要" :image-size="72" />
    <template v-else>
      <el-alert
        v-if="brief.missing_info?.length"
        :title="`还缺少：${brief.missing_info.join('、')}`"
        type="warning"
        :closable="false"
        show-icon
      />

      <el-form class="brief-form" label-position="top" @submit.prevent="save">
        <el-form-item label="教学目标">
          <el-input v-model="form.teaching_goal" type="textarea" :rows="2" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="授课对象">
          <el-input v-model="form.target_audience" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="课时（分钟）">
          <el-input-number v-model="form.duration_minutes" :min="1" :max="480" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="核心知识点（一行一个）">
          <el-input v-model="form.knowledge_points" type="textarea" :rows="3" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="逻辑顺序（一行一个）">
          <el-input v-model="form.logic_flow" type="textarea" :rows="2" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="教学重点">
          <el-input v-model="form.teaching_focus" type="textarea" :rows="2" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="教学难点">
          <el-input v-model="form.teaching_difficulties" type="textarea" :rows="2" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="产出类型">
          <el-select v-model="form.output_types" multiple :disabled="isConfirmed" style="width: 100%">
            <el-option label="PPT 课件" value="pptx" />
            <el-option label="Word 教案" value="docx" />
            <el-option label="PDF 打印版" value="pdf" />
            <el-option label="互动 HTML" value="html" />
          </el-select>
        </el-form-item>
        <el-form-item label="互动思路">
          <el-input v-model="form.interaction_ideas" type="textarea" :rows="2" :disabled="isConfirmed" />
        </el-form-item>
      </el-form>

      <div class="brief-actions">
        <el-button v-if="!isConfirmed" :loading="saving" @click="save">保存修改</el-button>
        <el-button
          type="primary"
          :disabled="isConfirmed || !brief.is_complete"
          :loading="confirming"
          @click="$emit('confirm')"
        >
          确认需求
        </el-button>
      </div>
    </template>
  </aside>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  brief: { type: Object, default: null },
  saving: { type: Boolean, default: false },
  confirming: { type: Boolean, default: false },
})

const emit = defineEmits(['save', 'confirm'])
const form = reactive({
  teaching_goal: '',
  target_audience: '',
  duration_minutes: 45,
  knowledge_points: '',
  logic_flow: '',
  teaching_focus: '',
  teaching_difficulties: '',
  output_types: [],
  interaction_ideas: '',
})

const isConfirmed = computed(() => props.brief?.status === 'confirmed')
const knowledgePointDetails = ref([])

function syncForm(brief) {
  if (!brief) return
  form.teaching_goal = brief.teaching_goal || ''
  form.target_audience = brief.target_audience || ''
  form.duration_minutes = brief.duration_minutes || 45
  knowledgePointDetails.value = (brief.knowledge_points || []).map(item => ({ ...item }))
  form.knowledge_points = knowledgePointDetails.value.map(item => item.title).join('\n')
  form.logic_flow = (brief.logic_flow || []).join('\n')
  form.teaching_focus = brief.teaching_focus || ''
  form.teaching_difficulties = brief.teaching_difficulties || ''
  form.output_types = [...(brief.output_types || [])]
  form.interaction_ideas = brief.interaction_ideas || ''
}

function save() {
  const previousByTitle = new Map(
    knowledgePointDetails.value.map(item => [item.title.trim(), item]),
  )
  emit('save', {
    ...form,
    knowledge_points: form.knowledge_points
      .split(/\r?\n/)
      .map((title, index) => {
        const normalizedTitle = title.trim()
        if (!normalizedTitle) return null
        const previous = previousByTitle.get(normalizedTitle) || knowledgePointDetails.value[index] || {}
        return {
          ...previous,
          order: index + 1,
          title: normalizedTitle,
          difficulty: previous.difficulty || 'basic',
          key_points: [...(previous.key_points || [])],
          examples: [...(previous.examples || [])],
          estimated_minutes: previous.estimated_minutes || 5,
        }
      })
      .filter(Boolean),
    logic_flow: form.logic_flow.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
  })
}

watch(() => props.brief, syncForm, { immediate: true, deep: true })
</script>

<style scoped>
.brief-panel {
  width: 340px;
  min-width: 300px;
  max-height: 100%;
  overflow: auto;
  padding: 18px;
  border-left: 1px solid #e6eaf0;
  background: #ffffff;
}

.brief-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.eyebrow {
  margin: 0 0 6px;
  color: #1463ff;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
}

h2 {
  margin: 0;
  color: #0f172a;
  font-size: 17px;
}

.brief-form {
  margin-top: 16px;
}

.brief-form :deep(.el-form-item) {
  margin-bottom: 14px;
}

.brief-form :deep(.el-input-number) {
  width: 100%;
}

.brief-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
}

@media (max-width: 980px) {
  .brief-panel {
    width: 100%;
    min-width: 0;
    max-height: none;
    border-top: 1px solid #e6eaf0;
    border-left: 0;
  }
}
</style>
