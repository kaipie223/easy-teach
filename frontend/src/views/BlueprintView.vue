<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>教学蓝图</h1>
        <p>PPT、教案、打印版和互动网页分别按当前蓝图生成。</p>
      </div>
      <div class="header-actions">
        <template v-if="editing">
          <el-button :disabled="saving" @click="cancelEditing"><el-icon><Close /></el-icon>取消</el-button>
          <el-button type="primary" :loading="saving" @click="saveRevision"><el-icon><Check /></el-icon>保存新版本</el-button>
        </template>
        <template v-else>
          <el-button :loading="loading" @click="loadPlan"><el-icon><Refresh /></el-icon>刷新</el-button>
          <el-button :disabled="!plan" @click="startEditing"><el-icon><EditPen /></el-icon>编辑蓝图</el-button>
          <el-button :loading="loading" :disabled="!projectId" @click="rebuildPlan('ai')"><el-icon><MagicStick /></el-icon>AI 重新生成</el-button>
          <el-button type="primary" :loading="generating" :disabled="!plan" @click="generate"><el-icon><MagicStick /></el-icon>生成并导出成果</el-button>
        </template>
      </div>
    </header>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false">
      <template v-if="canUseTemplate" #default><el-button size="small" @click="rebuildPlan('template')">使用基础模板</el-button></template>
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
      <section class="section-card">
        <div class="blueprint-summary">
          <div class="summary-item summary-title">
            <span>课程主题</span>
            <el-input v-if="editing" v-model="content.title" maxlength="120" show-word-limit />
            <strong v-else>{{ content.title }}</strong>
          </div>
          <div class="summary-item"><span>授课对象</span><strong>{{ content.target_audience || '未设置' }}</strong></div>
          <div class="summary-item"><span>课时</span><strong>{{ plan.duration_minutes }} 分钟</strong></div>
          <div class="summary-item"><span>蓝图版本</span><strong>v{{ plan.version }}</strong></div>
          <div class="summary-item"><span>生成方式</span><strong>{{ generationModeLabel(plan.generation_mode) }}</strong></div>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header"><div><h2>教学流程</h2><p>环节时长总和必须与课时一致，保存时会自动执行质量校验。</p></div></div>
        <div class="table-wrap">
          <table class="data-table editing-table">
            <thead><tr><th>环节</th><th>时间</th><th>目标</th><th>教师活动</th><th>学生活动</th><th>来源</th></tr></thead>
            <tbody>
              <tr v-for="section in content.lesson_sections" :key="section.section_id">
                <td><el-input v-if="editing" v-model="section.title" /><strong v-else>{{ section.order }}. {{ section.title }}</strong></td>
                <td><el-input-number v-if="editing" v-model="section.duration_minutes" :min="1" :max="480" controls-position="right" /><template v-else>{{ section.duration_minutes }} 分钟</template></td>
                <td><el-input v-if="editing" v-model="section.objective" type="textarea" :rows="3" /><template v-else>{{ section.objective }}</template></td>
                <td><el-input v-if="editing" :model-value="section.teacher_actions.join('\n')" type="textarea" :rows="3" @input="setLines(section, 'teacher_actions', $event)" /><template v-else>{{ section.teacher_actions.join('；') }}</template></td>
                <td><el-input v-if="editing" :model-value="section.student_actions.join('\n')" type="textarea" :rows="3" @input="setLines(section, 'student_actions', $event)" /><template v-else>{{ section.student_actions.join('；') }}</template></td>
                <td>{{ sourceLabel(section.evidence_refs) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header"><div><h2>PPT 页面与讲稿</h2><p>页面要点用于投影，讲稿用于教师备注，二者分开编辑。</p></div></div>
        <div class="slide-list">
          <article v-for="slide in content.slides" :key="slide.slide_id" class="slide-item">
            <div class="slide-order">{{ slide.order }}</div>
            <div class="slide-content">
              <template v-if="editing">
                <el-input v-model="slide.title" placeholder="页面标题" />
                <el-input v-model="slide.purpose" placeholder="页面目的" />
                <el-input :model-value="slide.bullets.join('\n')" type="textarea" :rows="3" placeholder="每行一个投影要点" @input="setLines(slide, 'bullets', $event)" />
                <el-input v-model="slide.speaker_notes" type="textarea" :rows="3" placeholder="教师讲稿" />
              </template>
              <template v-else>
                <strong>{{ slide.title }}</strong><span>{{ slide.purpose }}</span><p>{{ slide.bullets.join('；') }}</p><small>讲稿：{{ slide.speaker_notes || '暂无' }}</small>
              </template>
              <small>{{ sourceLabel(slide.evidence_refs) }}</small>
            </div>
          </article>
        </div>
      </section>

      <section class="section-card interaction-section">
        <div class="section-header"><div><h2>互动内容</h2><p>题目、提示语和反馈将进入可操作的 HTML 练习。</p></div></div>
        <div v-for="interaction in content.interactions" :key="interaction.interaction_id" class="interaction-row">
          <template v-if="editing">
            <el-input v-model="interaction.title" placeholder="互动标题" />
            <el-input v-model="interaction.prompt" type="textarea" :rows="2" placeholder="任务提示" />
            <div class="two-column-fields"><el-input v-model="interaction.feedback_correct" placeholder="答对反馈" /><el-input v-model="interaction.feedback_incorrect" placeholder="答错反馈" /></div>
            <el-input v-model="interaction.explanation" type="textarea" :rows="2" placeholder="答案解析" />
          </template>
          <template v-else><strong>{{ interaction.title }}</strong><span>{{ interaction.prompt }}</span></template>
          <div class="tag-row"><span v-for="item in interaction.items" :key="item" class="status-pill pending">{{ item }}</span></div>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header"><div><h2>四类成果规范</h2><p>这些字段会被对应导出器直接消费，而不是作为说明文字丢弃。</p></div></div>
        <div class="format-specs">
          <div class="format-spec"><strong>PPT 视觉方向</strong><el-input v-if="editing" v-model="content.output_specs.pptx.visual_direction" type="textarea" :rows="3" /><p v-else>{{ content.output_specs.pptx.visual_direction }}</p></div>
          <div class="format-spec"><strong>Word 课后任务</strong><el-input v-if="editing" v-model="content.output_specs.docx.homework" type="textarea" :rows="3" /><p v-else>{{ content.output_specs.docx.homework || '暂无' }}</p></div>
          <div class="format-spec"><strong>PDF 打印摘要</strong><el-input v-if="editing" v-model="content.output_specs.pdf.printable_summary" type="textarea" :rows="3" /><p v-else>{{ content.output_specs.pdf.printable_summary || '暂无' }}</p></div>
          <div class="format-spec"><strong>HTML 完成反馈</strong><el-input v-if="editing" v-model="content.output_specs.html.completion_message" type="textarea" :rows="3" /><p v-else>{{ content.output_specs.html.completion_message }}</p></div>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header"><div><h2>来源证据</h2><p>来源会写入 PPT、教案和打印版。</p></div></div>
        <div v-if="plan.source_refs.length" class="source-list">
          <div v-for="ref in plan.source_refs" :key="ref.evidence_id || `${ref.source_name}-${ref.quote}`" class="source-row"><el-icon><CircleCheck /></el-icon><span>{{ sourceLabel([ref]) }}</span><small>{{ ref.quote }}</small></div>
        </div>
        <el-empty v-else description="当前蓝图没有关联证据" :image-size="72" />
      </section>
    </template>

    <el-empty v-else description="请从项目会话进入教学蓝图" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Check, CircleCheck, Close, EditPen, MagicStick, Refresh } from '@element-plus/icons-vue'
import { createVersionExports, fetchArtifactVersions, fetchCoursewarePlan, saveCoursewarePlanRevision } from '@/api'
import StageProgress from '@/components/progress/StageProgress.vue'
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

function clone(value) { return JSON.parse(JSON.stringify(value)) }
function generationModeLabel(mode) { return { ai: 'AI 生成', template: '基础模板', manual: '教师修订' }[mode] || mode }
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

/** 把 SSE 错误包装成与 axios 同构的形状，调用方的 catch 逻辑无需改动。 */
function streamError(details) {
  return { response: { data: { error: details } } }
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
    catch (error) { if (error.response?.status !== 404) throw error; plan.value = (await buildPlanWithProgress({ generationMode: 'ai' })).data }
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
    plan.value = (await buildPlanWithProgress({ forceRebuild: true, generationMode })).data
    cancelEditing()
    ElMessage.success(generationMode === 'ai' ? 'AI 教学蓝图已生成' : '基础模板蓝图已生成')
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '教学蓝图生成失败'
    canUseTemplate.value = generationMode === 'ai' && (error.response?.data?.error?.code?.startsWith('AI_') || false)
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
.slide-list, .source-list { display: grid; gap: 10px; }
.slide-item { display: grid; grid-template-columns: 36px 1fr; gap: 12px; padding: 12px 0; border-bottom: 1px solid #e6eaf0; }
.slide-item:last-child { border-bottom: 0; }
.slide-order { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 6px; background: #eef4ff; color: #1463ff; font-weight: 700; }
.slide-content, .interaction-row { display: grid; gap: 10px; }
.slide-content span, .slide-content small, .interaction-row span, .source-row small { color: #64748b; }
.slide-content p, .format-spec p { margin: 4px 0; white-space: pre-wrap; }
.tag-row { display: flex; flex-wrap: wrap; gap: 8px; }
.source-row { display: grid; grid-template-columns: auto minmax(160px, .7fr) 1fr; gap: 10px; align-items: baseline; }
.summary-title { min-width: 260px; }
.editing-table { min-width: 1180px; }
.editing-table :deep(.el-input-number) { width: 110px; }
.two-column-fields, .format-specs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.format-spec { display: grid; align-content: start; gap: 8px; padding: 4px 0 14px; border-bottom: 1px solid #e6eaf0; }
@media (max-width: 900px) {
  .source-row { grid-template-columns: auto 1fr; }
  .source-row small { grid-column: 2; }
  .two-column-fields, .format-specs { grid-template-columns: 1fr; }
}
</style>
