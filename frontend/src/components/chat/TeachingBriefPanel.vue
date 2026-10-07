<template>
  <aside class="brief-panel" aria-labelledby="brief-title">
    <div class="brief-header">
      <div class="brief-heading">
        <p class="eyebrow">需求确认单</p>
        <h2 id="brief-title">这节课的信息</h2>
      </div>
      <el-tag
        v-if="brief"
        size="small"
        :type="brief.status === 'confirmed' ? 'success' : 'info'"
      >
        {{ brief.status === 'confirmed' ? '已确认' : `草稿 v${brief.version}` }}
      </el-tag>
    </div>

    <!-- 已确认是个持久的"完成"状态：托托在这里把话说完整，比一个绿标签更明确 -->
    <TotoMascot v-if="isConfirmed" class="brief-toto" state="success" :size="104" />

    <div v-if="!brief" class="brief-empty">
      <el-icon><Document /></el-icon>
      <p>发一条教学想法，这里会自动整理成需求单。</p>
    </div>

    <template v-else>
      <!-- 进度：让"正在推进"看得见。待确认项做成一枚枚标签，确认一项就少一枚，
           正在追问的那一项高亮 —— 卡片与需求单由此对得上。以前这里只有一行
           "还需要补充：…"，教师看不出到底是 1 项还是 8 项没定。 -->
      <div v-if="!isConfirmed && coreTotal" class="brief-progress" aria-live="polite">
        <div class="progress-head">
          <span class="progress-count">已确认 {{ confirmedCount }}/{{ coreTotal }} 项</span>
          <span class="progress-state">{{ progressState }}</span>
        </div>
        <el-progress
          class="progress-bar"
          :percentage="progressPercent"
          :show-text="false"
          :stroke-width="6"
        />
        <div v-if="pendingFields.length" class="progress-chips">
          <span
            v-for="key in pendingFields"
            :key="key"
            class="pending-chip"
            :class="{ 'is-active': key === activeField }"
          >
            {{ fieldLabel(key) }}
          </span>
        </div>
      </div>

      <el-alert
        v-if="extraMissing.length"
        class="brief-alert"
        :title="`还需要补充：${extraMissing.join('、')}`"
        type="warning"
        :closable="false"
        show-icon
        aria-live="polite"
      />

      <el-form class="brief-form" label-position="top" @submit.prevent="save">
        <el-form-item label="课程／课题">
          <el-input v-model="form.course_name" :disabled="isConfirmed" />
        </el-form-item>
        <el-form-item label="学科">
          <el-input
            v-model="form.subject"
            placeholder="如：语文、数学、化学、信息技术"
            :disabled="isConfirmed"
          />
        </el-form-item>
        <el-form-item label="年级／学段">
          <el-input v-model="form.grade" placeholder="如：高一、小学三年级" :disabled="isConfirmed" />
        </el-form-item>
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
          <el-input
            v-model="form.teaching_difficulties"
            type="textarea"
            :rows="2"
            :disabled="isConfirmed"
          />
        </el-form-item>
        <el-form-item label="产出类型">
          <el-select v-model="form.output_types" multiple :disabled="isConfirmed" class="brief-select">
            <el-option label="PPT 课件" value="pptx" />
            <el-option label="Word 教案" value="docx" />
            <el-option label="PDF 打印版" value="pdf" />
            <el-option label="互动 HTML" value="html" />
          </el-select>
        </el-form-item>
        <el-form-item label="互动思路">
          <el-input
            v-model="form.interaction_ideas"
            type="textarea"
            :rows="2"
            :disabled="isConfirmed"
          />
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
          {{ isConfirmed ? '进入下一步' : '确认需求' }}
        </el-button>
      </div>
    </template>
  </aside>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Document } from '@element-plus/icons-vue'
import TotoMascot from '@/components/common/TotoMascot.vue'

const props = defineProps({
  brief: { type: Object, default: null },
  saving: { type: Boolean, default: false },
  confirming: { type: Boolean, default: false },
  /** 当前追问的字段名（由对话事件下发）：把追问卡片和右侧表单对应起来 */
  activeField: { type: String, default: '' },
})

const emit = defineEmits(['save', 'confirm'])

/** 核心字段的中文名：待确认标签与"正在问"提示用。
    措辞与后端 FIELD_LABELS 保持一致 —— 卡片上写"年级／学段"，标签也必须是同一个词，
    否则教师对不上"现在问的是哪一项"。 */
const FIELD_LABELS = {
  grade: '年级／学段',
  subject: '学科',
  target_audience: '授课对象',
  duration_minutes: '课时长度',
  teaching_goal: '教学目标',
  knowledge_points: '核心知识点',
  teaching_focus: '教学重点',
  teaching_difficulties: '教学难点',
  output_types: '产出类型',
  logic_flow: '知识点逻辑顺序',
}

function fieldLabel(key) {
  return FIELD_LABELS[key] || key
}
const form = reactive({
  course_name: '',
  subject: '',
  grade: '',
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

// 进度以后端下发的 pending_fields 为准：课时有默认值 45，前端自己判断不出"算不算
// 已确认"，只有后端知道 source_refs 里到底有没有真定过。
const coreTotal = computed(() => props.brief?.core_total || 0)
const pendingFields = computed(() => props.brief?.pending_fields || [])
const confirmedCount = computed(() => Math.max(0, coreTotal.value - pendingFields.value.length))
const progressPercent = computed(() =>
  coreTotal.value ? Math.round((confirmedCount.value / coreTotal.value) * 100) : 0,
)
const activeLabel = computed(() => fieldLabel(props.activeField))

/** 一行状态：正在问哪一项 → 等老师补充 → 信息齐了（开场还没开始问时是中间那种） */
const progressState = computed(() => {
  if (!pendingFields.value.length) return '信息齐了，可以确认'
  return props.activeField ? `正在问：${activeLabel.value}` : '等你补充'
})

/**
 * 模型额外指出的缺失项单独提示。
 *
 * 这里必须排掉**所有**核心字段的标签，而不是只排"待确认"那几个：模型的 missing_info
 * 是自由文本，经常把已经确认的课时/教学目标也列进去，于是面板一边写"已确认 7/10"、
 * 一边又提示"还需要补充：课时长度"，两个口径互相打脸。
 */
const CORE_LABELS = new Set(Object.values(FIELD_LABELS))
const extraMissing = computed(() =>
  (props.brief?.missing_info || []).filter(label => !CORE_LABELS.has(label)),
)

function syncForm(brief) {
  if (!brief) return
  form.course_name = brief.course_name || ''
  form.subject = brief.subject || ''
  form.grade = brief.grade || ''
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
/* 宽度由外层 .chat-aside 决定，这里只管内部结构与留白 */
.brief-panel {
  width: 100%;
  display: flex;
  flex-direction: column;
  padding: var(--space-5);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-xl);
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.brief-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--border-hairline);
}

.brief-heading { min-width: 0; }

.brief-header h2 {
  margin-top: var(--space-1);
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.brief-toto {
  margin: var(--space-4) 0;
}

.brief-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-10) var(--space-4);
  text-align: center;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.brief-empty .el-icon {
  font-size: var(--text-xl);
  color: var(--neutral-400);
}

/* ── 进度：已确认 x/y + 待确认标签 ─────────────────────────── */
.brief-progress {
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
}

.progress-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.progress-count {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.progress-state {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.progress-bar :deep(.el-progress-bar__outer) {
  background: var(--border-light);
}

.progress-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  margin-top: var(--space-3);
}

/* 待确认项：确认一项就少一枚；正在追问的那一项用品牌色标出来 */
.pending-chip {
  padding: 2px var(--space-2);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-full);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.pending-chip.is-active {
  border-color: var(--border-brand);
  background: var(--bg-active);
  color: var(--text-brand);
  font-weight: var(--weight-medium);
}

.brief-alert {
  margin-top: var(--space-4);
}

.brief-form {
  margin-top: var(--space-5);
}

.brief-form :deep(.el-form-item) {
  margin-bottom: var(--space-4);
}

.brief-form :deep(.el-input-number),
.brief-select {
  width: 100%;
}

/* 操作固定在面板底部，长表单滚动时也够得着 */
.brief-actions {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding-top: var(--space-4);
  background: linear-gradient(to top, var(--bg-surface) 72%, transparent);
}

@media (max-width: 1024px) {
  .brief-panel {
    max-height: none;
  }

  .brief-actions > :deep(.el-button) { flex: 1; }
}
</style>
