<template>
  <div class="page-stack">
    <AppPageHeader
      title="教学蓝图"
      subtitle="PPT、教案、打印版与互动网页都按这一版蓝图生成。"
    >
      <template #actions>
        <template v-if="editing">
          <el-button :disabled="saving" @click="cancelEditing">取消</el-button>
          <el-button type="primary" :loading="saving" @click="saveRevision">
            <el-icon><Check /></el-icon>
            保存新版本
          </el-button>
        </template>
        <template v-else>
          <el-button :disabled="!plan" @click="startEditing">
            <el-icon><EditPen /></el-icon>
            编辑蓝图
          </el-button>
          <el-button type="primary" :loading="generating" :disabled="!plan" @click="generate">
            <el-icon><MagicStick /></el-icon>
            生成并导出成果
          </el-button>
        </template>
      </template>
    </AppPageHeader>

    <!-- 工具条：版本信息常显；昂贵且低频的操作（重新生成）收在这一行并要求二次确认 -->
    <div v-if="plan && !editing" class="plan-toolbar">
      <div class="toolbar-meta">
        <span class="status-pill running">v{{ plan.version }}</span>
        <span class="toolbar-text">
          {{ generationModeLabel(plan.generation_mode) }} · {{ plan.duration_minutes }} 分钟
        </span>
        <label
          class="thinking-toggle"
          title="开启后模型先推理再作答：内容更深入、讲稿与成果设定更完整，代价是生成更慢。只影响 AI 生成，基础模板不受影响。"
        >
          <span>深度思考</span>
          <el-switch
            :model-value="deepThinking"
            :disabled="loading"
            size="small"
            @change="setDeepThinking"
          />
        </label>
      </div>
      <div class="toolbar-actions">
        <el-button text :loading="loading" @click="loadPlan">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button text :loading="loading" :disabled="!projectId" @click="confirmRebuild">
          <el-icon><MagicStick /></el-icon>
          AI 重新生成
        </el-button>
      </div>
    </div>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false">
      <template v-if="canUseTemplate" #default>
        <el-button size="small" type="primary" @click="rebuildPlan('template')">使用基础模板</el-button>
      </template>
    </el-alert>

    <StageProgress
      v-if="loading"
      :percent="stagePercent"
      :label="stageLabel"
      :steps="stageList"
      :current="stageKey"
      :started-at="stageStartedAt"
    />
    <el-skeleton v-if="loading && !plan" :rows="8" animated />

    <template v-else-if="plan && content">
      <!-- 概览：一句话说清这版蓝图是什么，数字统一等宽 -->
      <section class="section-card plan-overview">
        <div class="overview-head">
          <el-input
            v-if="editing"
            v-model="content.title"
            maxlength="120"
            show-word-limit
            class="overview-title-input"
          />
          <h2 v-else>{{ content.title }}</h2>
          <!-- 生成时 title 常常就取自教学目标：完全重复时不再显示第二遍 -->
          <p v-if="content.teaching_goal && content.teaching_goal.trim() !== content.title.trim()">
            {{ content.teaching_goal }}
          </p>
        </div>

        <dl class="overview-grid">
          <div><dt>授课对象</dt><dd>{{ content.target_audience || '未设置' }}</dd></div>
          <div><dt>课时</dt><dd class="text-tabular">{{ plan.duration_minutes }} 分钟</dd></div>
          <div><dt>教学环节</dt><dd class="text-tabular">{{ content.lesson_sections.length }} 个</dd></div>
          <div><dt>投影页数</dt><dd class="text-tabular">{{ content.slides.length }} 页</dd></div>
          <div><dt>互动题</dt><dd class="text-tabular">{{ content.interactions.length }} 道</dd></div>
        </dl>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>教学流程</h2>
            <p>环节时长总和必须与课时一致，保存时会自动执行质量校验。</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table flow-table">
            <thead>
              <tr>
                <th>环节</th><th>时间</th><th>目标</th><th>教师活动</th><th>学生活动</th><th>来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="section in content.lesson_sections" :key="section.section_id">
                <td>
                  <el-input v-if="editing" v-model="section.title" />
                  <strong v-else>{{ section.order }}. {{ section.title }}</strong>
                </td>
                <td class="text-tabular">
                  <el-input-number
                    v-if="editing"
                    v-model="section.duration_minutes"
                    :min="1"
                    :max="480"
                    controls-position="right"
                  />
                  <template v-else>{{ section.duration_minutes }} 分钟</template>
                </td>
                <td>
                  <el-input v-if="editing" v-model="section.objective" type="textarea" :rows="3" />
                  <template v-else>{{ section.objective }}</template>
                </td>
                <td>
                  <el-input
                    v-if="editing"
                    :model-value="section.teacher_actions.join('\n')"
                    type="textarea"
                    :rows="3"
                    @input="setLines(section, 'teacher_actions', $event)"
                  />
                  <template v-else>{{ section.teacher_actions.join('；') }}</template>
                </td>
                <td>
                  <el-input
                    v-if="editing"
                    :model-value="section.student_actions.join('\n')"
                    type="textarea"
                    :rows="3"
                    @input="setLines(section, 'student_actions', $event)"
                  />
                  <template v-else>{{ section.student_actions.join('；') }}</template>
                </td>
                <td class="source-cell">{{ sourceLabel(section.evidence_refs) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>PPT 页面与讲稿</h2>
            <p>页面要点用于投影，讲稿用于教师备注，二者分开编辑。</p>
          </div>
        </div>
        <ol class="slide-list">
          <li v-for="slide in content.slides" :key="slide.slide_id" class="slide-item">
            <span class="slide-order text-tabular" aria-hidden="true">{{ slide.order }}</span>
            <div class="slide-body">
              <template v-if="editing">
                <el-input v-model="slide.title" placeholder="页面标题" />
                <el-input v-model="slide.purpose" placeholder="页面目的" />
                <el-input
                  :model-value="slide.bullets.join('\n')"
                  type="textarea"
                  :rows="3"
                  placeholder="每行一个投影要点"
                  @input="setLines(slide, 'bullets', $event)"
                />
                <el-input v-model="slide.speaker_notes" type="textarea" :rows="3" placeholder="教师讲稿" />
              </template>
              <template v-else>
                <div class="slide-head">
                  <strong>{{ slide.title }}</strong>
                  <span class="slide-purpose">{{ slide.purpose }}</span>
                </div>
                <ul class="bullet-list">
                  <li v-for="(bullet, index) in slide.bullets" :key="index">{{ bullet }}</li>
                </ul>
                <!-- 讲稿默认折叠：一册十几页全展开会把页面拉得很长 -->
                <details v-if="slide.speaker_notes" class="notes">
                  <summary>教师讲稿</summary>
                  <p>{{ slide.speaker_notes }}</p>
                </details>
              </template>
            </div>
          </li>
        </ol>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>互动内容</h2>
            <p>题目、提示语与反馈会进入可操作的 HTML 练习。</p>
          </div>
        </div>
        <div class="interaction-list">
          <article
            v-for="interaction in content.interactions"
            :key="interaction.interaction_id"
            class="interaction-card"
          >
            <template v-if="editing">
              <el-input v-model="interaction.title" placeholder="互动标题" />
              <el-input v-model="interaction.prompt" type="textarea" :rows="2" placeholder="任务提示" />
              <div class="two-column-fields">
                <el-input v-model="interaction.feedback_correct" placeholder="答对反馈" />
                <el-input v-model="interaction.feedback_incorrect" placeholder="答错反馈" />
              </div>
              <el-input v-model="interaction.explanation" type="textarea" :rows="2" placeholder="答案解析" />
            </template>
            <template v-else>
              <div class="interaction-head">
                <strong>{{ interaction.title }}</strong>
                <span class="status-pill pending">{{ interactionTypeLabel(interaction.interaction_type) }}</span>
              </div>
              <p class="interaction-prompt">{{ interaction.prompt }}</p>
            </template>

            <div class="tag-row">
              <span v-for="item in interaction.items" :key="item" class="item-pill">{{ item }}</span>
            </div>
          </article>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>四类成果规范</h2>
            <p>这些字段会被对应导出器直接消费，而不是作为说明文字丢弃。</p>
          </div>
        </div>
        <div class="format-specs">
          <div class="format-spec">
            <span class="spec-mark is-pptx" aria-hidden="true"><el-icon><Monitor /></el-icon></span>
            <strong>PPT 视觉方向</strong>
            <el-input
              v-if="editing"
              v-model="content.output_specs.pptx.visual_direction"
              type="textarea"
              :rows="3"
            />
            <p v-else>{{ content.output_specs.pptx.visual_direction }}</p>
          </div>
          <div class="format-spec">
            <span class="spec-mark is-docx" aria-hidden="true"><el-icon><Document /></el-icon></span>
            <strong>Word 课后任务</strong>
            <el-input
              v-if="editing"
              v-model="content.output_specs.docx.homework"
              type="textarea"
              :rows="3"
            />
            <p v-else>{{ content.output_specs.docx.homework || '暂无' }}</p>
          </div>
          <div class="format-spec">
            <span class="spec-mark is-pdf" aria-hidden="true"><el-icon><Printer /></el-icon></span>
            <strong>PDF 打印摘要</strong>
            <el-input
              v-if="editing"
              v-model="content.output_specs.pdf.printable_summary"
              type="textarea"
              :rows="3"
            />
            <p v-else>{{ content.output_specs.pdf.printable_summary || '暂无' }}</p>
          </div>
          <div class="format-spec">
            <span class="spec-mark is-html" aria-hidden="true"><el-icon><MagicStick /></el-icon></span>
            <strong>HTML 完成反馈</strong>
            <el-input
              v-if="editing"
              v-model="content.output_specs.html.completion_message"
              type="textarea"
              :rows="3"
            />
            <p v-else>{{ content.output_specs.html.completion_message }}</p>
          </div>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>来源证据</h2>
            <p>来源会写入 PPT、教案和打印版。</p>
          </div>
        </div>
        <ul v-if="plan.source_refs.length" class="source-list">
          <li
            v-for="ref in plan.source_refs"
            :key="ref.evidence_id || `${ref.source_name}-${ref.quote}`"
            class="source-row"
          >
            <el-icon class="source-icon" aria-hidden="true"><CircleCheck /></el-icon>
            <span class="source-name">{{ sourceLabel([ref]) }}</span>
            <small class="source-quote">{{ ref.quote }}</small>
          </li>
        </ul>
        <p v-else class="list-hint">当前蓝图没有关联证据，教师可在资料中心补充参考资料。</p>
      </section>
    </template>

    <AppEmptyState
      v-else
      :icon="Reading"
      title="还没有教学蓝图"
      description="先在需求共创里确认需求单，这里会按需求单生成可编辑的蓝图。"
    >
      <el-button type="primary" @click="router.push('/requirements')">回到需求共创</el-button>
    </AppEmptyState>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Check,
  CircleCheck,
  Document,
  EditPen,
  MagicStick,
  Monitor,
  Printer,
  Reading,
  Refresh,
} from '@element-plus/icons-vue'
import { createVersionExports, fetchArtifactVersions, fetchCoursewarePlan, saveCoursewarePlanRevision } from '@/api'
import StageProgress from '@/components/progress/StageProgress.vue'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import { SSEClient } from '@/utils/sse'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => projectStore.activeProjectId)
const plan = ref(null)
const draft = ref(null)
const editing = ref(false)
const loading = ref(false)
const saving = ref(false)
const generating = ref(false)
const errorMessage = ref('')
const canUseTemplate = ref(false)
const stageLabel = ref('')
const stagePercent = ref(0)
const stageKey = ref('')
const stageList = ref([])
// 已用时长纯前端算，后端只需要给开始时刻
const stageStartedAt = ref(null)
const content = computed(() => editing.value ? draft.value : plan.value?.content)

// 深度思考开关：默认开启，和服务端的质量优先默认值一致；关掉它换取更快的生成。
// 记在 localStorage，避免每次进页面都要重新选一遍。
const DEEP_THINKING_KEY = 'easy-teach:deep-thinking'
const deepThinking = ref(localStorage.getItem(DEEP_THINKING_KEY) !== 'false')
function setDeepThinking(value) {
  deepThinking.value = Boolean(value)
  localStorage.setItem(DEEP_THINKING_KEY, String(deepThinking.value))
}

function clone(value) { return JSON.parse(JSON.stringify(value)) }
function generationModeLabel(mode) { return { ai: 'AI 生成', template: '基础模板', manual: '教师修订' }[mode] || mode }
function interactionTypeLabel(type) {
  return { matching: '配对', classification: '分类', ordering: '排序', quiz: '选择' }[type] || type || '互动'
}

/** 重新生成会消耗一次模型额度，必须先确认再做。 */
async function confirmRebuild() {
  try {
    await ElMessageBox.confirm(
      '重新生成会按最新需求单完整重建这一版蓝图，并消耗一次模型额度。确定继续？',
      'AI 重新生成',
      { confirmButtonText: '重新生成', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  await rebuildPlan('ai')
}
function setLines(target, key, value) { target[key] = String(value).split(/\r?\n/).map(item => item.trim()).filter(Boolean) }
function sourceLabel(refs) {
  if (!refs?.length) return '暂无'
  return refs.map((ref) => {
    const locator = ref.locator || {}
    const location = ['page', 'slide', 'timestamp', 'paragraph'].filter(key => locator[key] !== undefined).map(key => `${key} ${locator[key]}`).join(', ')
    return location ? `${ref.source_name} (${location})` : ref.source_name
  }).join('；')
}
function startEditing() { draft.value = clone(plan.value.content); editing.value = true; errorMessage.value = '' }
function cancelEditing() { draft.value = null; editing.value = false }

async function saveRevision() {
  if (!draft.value || saving.value) return
  saving.value = true
  errorMessage.value = ''
  try {
    plan.value = (await saveCoursewarePlanRevision(projectId.value, plan.value.plan_id, draft.value, '教师在教学蓝图页面完成修订')).data
    cancelEditing()
    ElMessage.success('教学蓝图新版本已保存')
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '蓝图保存失败，请检查内容后重试'
  } finally { saving.value = false }
}

/**
 * 把 SSE 错误包装成与 axios 同构的形状，调用方的 catch 逻辑无需改动。
 *
 * SSE 错误帧的形状是 {content, data:{code, recoverable, suggested_action}}，
 * 而页面读的是 error.message / error.code —— 直接透传会让每一处提示都退化成
 * 兜底文案（教师看不到真实原因，也拿不到可降级的错误码），所以这里统一形状。
 */
function streamError(details) {
  const payload = details || {}
  const data = payload.data || {}
  return {
    response: {
      data: {
        error: {
          message: payload.message || payload.content || '教学蓝图生成失败，请重试',
          code: payload.code || data.code,
          recoverable: payload.recoverable ?? data.recoverable,
          suggested_action: payload.suggested_action || data.suggested_action,
        },
      },
    },
  }
}

/**
 * 通过 SSE 生成教学蓝图：生成期间实时展示阶段文案，结束时拿到完整蓝图。
 * 生成通常需要一到数分钟，流式阶段反馈避免页面长时间无响应。
 */
async function buildPlanWithProgress(options) {
  let failure = null
  let result = null
  stageLabel.value = '正在准备生成教学蓝图……'
  stagePercent.value = 0
  stageKey.value = ''
  stageList.value = []
  stageStartedAt.value = new Date()

  const client = new SSEClient(`/api/v1/projects/${projectId.value}/plan`, {
    onProgress: (payload) => {
      stageLabel.value = payload?.stage_label || payload?.label || stageLabel.value
      stagePercent.value = payload?.percent ?? stagePercent.value
      stageKey.value = payload?.stage || stageKey.value
      if (payload?.stages?.length) stageList.value = payload.stages
    },
    onResult: (payload) => { result = payload },
    onServiceError: (details) => { failure = streamError(details) },
    onError: () => { failure = streamError({ code: 'PLAN_STREAM_DISCONNECTED', message: '生成连接中断，请重试' }) },
  })

  await client.connect({
    force_rebuild: Boolean(options.forceRebuild),
    generation_mode: options.generationMode || 'ai',
    allow_template_fallback: Boolean(options.allowTemplateFallback),
    // 只有教师明确选过才发送：不发送等于"按服务端配置"，行为与改造前一致
    ...(typeof options.deepThinking === 'boolean' ? { deep_thinking: options.deepThinking } : {}),
  })
  stageLabel.value = ''

  if (failure) throw failure
  if (!result) throw streamError({ code: 'PLAN_STREAM_INCOMPLETE', message: '教学蓝图生成未完成，请重试' })
  return { data: result }
}

async function loadPlan() {
  if (!projectId.value) { errorMessage.value = '缺少项目 ID，请从项目会话进入教学蓝图'; return }
  loading.value = true
  errorMessage.value = ''
  canUseTemplate.value = false
  try {
    try { plan.value = (await fetchCoursewarePlan(projectId.value)).data }
    catch (error) { if (error.response?.status !== 404) throw error; plan.value = (await buildPlanWithProgress({ generationMode: 'ai', deepThinking: deepThinking.value })).data }
    cancelEditing()
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '教学蓝图加载失败，请先确认需求'
    canUseTemplate.value = error.response?.data?.error?.code?.startsWith('AI_') || false
  } finally { loading.value = false }
}

async function rebuildPlan(generationMode) {
  if (!projectId.value || loading.value) return
  loading.value = true
  errorMessage.value = ''
  canUseTemplate.value = false
  try {
    plan.value = (await buildPlanWithProgress({
      forceRebuild: true,
      generationMode,
      deepThinking: deepThinking.value,
    })).data
    cancelEditing()
    ElMessage.success(
      generationMode === 'ai'
        ? (deepThinking.value ? 'AI 教学蓝图已生成（深度思考）' : 'AI 教学蓝图已生成')
        : '基础模板蓝图已生成'
    )
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '教学蓝图生成失败'
    // AI 生成失败时始终给一条"改用基础模板"的后路：模板是纯后端编译，不会因为
    // 模型输出不合规而失败，否则教师只剩反复重试这一条死路。
    canUseTemplate.value = generationMode === 'ai'
  } finally { loading.value = false }
}

async function generate() {
  if (!plan.value || generating.value) return
  generating.value = true
  errorMessage.value = ''
  try {
    const versions = (await fetchArtifactVersions(projectId.value)).data || []
    const version = versions.find(item => item.source_plan_id === plan.value.plan_id) || versions[0]
    if (!version) throw new Error('教学蓝图尚未建立成果版本')
    await createVersionExports(projectId.value, version.artifact_version_id)
    ElMessage.success('四类成果导出任务已创建')
    router.push({ path: '/exports', query: { artifactVersionId: version.artifact_version_id } })
  } catch (error) { errorMessage.value = error.response?.data?.error?.message || '成果生成失败，请重试' }
  finally { generating.value = false }
}

onMounted(loadPlan)
</script>

<style scoped>
/* ── 工具条：版本信息常显，昂贵操作靠右且是文字按钮 ───────── */
.plan-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.toolbar-meta,
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.toolbar-text {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.thinking-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
}

/* ── 概览 ───────────────────────────────────────────────── */
.overview-head h2 {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.overview-title-input { max-width: 560px; }

.overview-head p {
  margin-top: var(--space-2);
  max-width: 76ch;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-4);
  margin: var(--space-5) 0 0;
  padding-top: var(--space-5);
  border-top: 1px solid var(--border-hairline);
}

.overview-grid div { min-width: 0; }

.overview-grid dt {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.overview-grid dd {
  margin: var(--space-1) 0 0;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
}

/* ── 教学流程表：窄屏横向滚动而不是压扁列 ───────────────── */
.flow-table { min-width: 1040px; }

/* 长文本列给足宽度，否则每一行都会被折成很多行，表格难扫读 */
.flow-table th:nth-child(1), .flow-table td:nth-child(1) { width: 128px; }
.flow-table th:nth-child(2), .flow-table td:nth-child(2) { width: 88px; }
.flow-table th:nth-child(3), .flow-table td:nth-child(3) { width: 22%; }
.flow-table th:nth-child(4), .flow-table td:nth-child(4) { width: 26%; }
.flow-table th:nth-child(5), .flow-table td:nth-child(5) { width: 26%; }
.flow-table th:nth-child(6), .flow-table td:nth-child(6) { width: 140px; }

.flow-table :deep(.el-input-number) { width: 120px; }

.flow-table td { vertical-align: top; }

.source-cell {
  max-width: 200px;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

/* ── PPT 页面与讲稿 ─────────────────────────────────────── */
.slide-list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.slide-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: var(--space-4);
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--border-hairline);
}

.slide-item:last-child { border-bottom: 0; }

.slide-order {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--brand-50);
  color: var(--text-brand);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.slide-body { display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; }

.slide-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.slide-head strong {
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.slide-purpose {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.bullet-list {
  margin: 0;
  padding-left: var(--space-5);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
}

/* 讲稿折叠：原生 details，无 JS、可键盘操作 */
.notes summary {
  color: var(--text-brand);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.notes p {
  margin: var(--space-2) 0 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
  white-space: pre-wrap;
}

/* ── 互动内容 ───────────────────────────────────────────── */
.interaction-list { display: grid; gap: var(--space-4); }

.interaction-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-lg);
  background: var(--bg-surface-sunken);
}

.interaction-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.interaction-head strong {
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.interaction-prompt {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.tag-row { display: flex; flex-wrap: wrap; gap: var(--space-2); }

.item-pill {
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-pill);
  background: var(--bg-surface);
  border: 1px solid var(--border-hairline);
  color: var(--text-secondary);
  font-size: var(--text-xs);
}

/* ── 成果规范 ───────────────────────────────────────────── */
.format-specs,
.two-column-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-4);
}

.format-spec {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-lg);
}

.spec-mark {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  margin-bottom: var(--space-1);
  border-radius: var(--radius-md);
  background: var(--bg-surface-sunken);
  font-size: var(--text-base);
}

.spec-mark.is-pptx { color: var(--file-ppt); }
.spec-mark.is-docx { color: var(--file-word); }
.spec-mark.is-pdf { color: var(--file-pdf); }
.spec-mark.is-html { color: var(--text-brand); }

.format-spec strong {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.format-spec p {
  color: var(--text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
  white-space: pre-wrap;
}

/* ── 来源证据 ───────────────────────────────────────────── */
.source-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.source-row {
  display: grid;
  grid-template-columns: 20px minmax(180px, 0.6fr) minmax(0, 1fr);
  gap: var(--space-3);
  align-items: baseline;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border-hairline);
}

.source-row:last-child { border-bottom: 0; }

.source-icon { color: var(--success-500); }

.source-name {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.source-quote {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

.list-hint {
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

@media (max-width: 768px) {
  .plan-toolbar {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
  }

  .toolbar-actions { width: 100%; }

  .source-row {
    grid-template-columns: 20px minmax(0, 1fr);
    gap: var(--space-1) var(--space-3);
  }

  .source-quote { grid-column: 2; }
}
</style>
