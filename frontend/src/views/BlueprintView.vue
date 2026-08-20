<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>教学蓝图</h1>
        <p>由已确认的教学需求和资料证据生成，三类成果共享同一份蓝图。</p>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="loadPlan">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" :loading="generating" :disabled="!plan" @click="generate">
          <el-icon><MagicStick /></el-icon>
          生成成果
        </el-button>
      </div>
    </header>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-skeleton v-if="loading && !plan" :rows="8" animated />

    <template v-else-if="plan">
      <section class="section-card">
        <div class="blueprint-summary">
          <div class="summary-item">
            <span>课程主题</span>
            <strong>{{ plan.content.title }}</strong>
          </div>
          <div class="summary-item">
            <span>授课对象</span>
            <strong>{{ plan.content.target_audience || '未设置' }}</strong>
          </div>
          <div class="summary-item">
            <span>课时</span>
            <strong>{{ plan.duration_minutes }} 分钟</strong>
          </div>
          <div class="summary-item">
            <span>蓝图版本</span>
            <strong>v{{ plan.version }}</strong>
          </div>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>教学流程</h2>
            <p>每个环节都包含目标、师生活动和来源证据。</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>环节</th>
                <th>时间</th>
                <th>目标</th>
                <th>教师活动</th>
                <th>学生活动</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="section in plan.content.lesson_sections" :key="section.section_id">
                <td><strong>{{ section.order }}. {{ section.title }}</strong></td>
                <td>{{ section.duration_minutes }} 分钟</td>
                <td>{{ section.objective }}</td>
                <td>{{ section.teacher_actions.join('；') }}</td>
                <td>{{ section.student_actions.join('；') }}</td>
                <td>{{ sourceLabel(section.evidence_refs) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>页面结构</h2>
            <p>下面的 SlideSpec 将同时驱动 PPT 和成果预览。</p>
          </div>
        </div>
        <div class="slide-list">
          <article v-for="slide in plan.content.slides" :key="slide.slide_id" class="slide-item">
            <div class="slide-order">{{ slide.order }}</div>
            <div class="slide-content">
              <strong>{{ slide.title }}</strong>
              <span>{{ slide.purpose }}</span>
              <p>{{ slide.bullets.join('；') }}</p>
              <small>{{ sourceLabel(slide.evidence_refs) }}</small>
            </div>
          </article>
        </div>
      </section>

      <section class="section-card interaction-section">
        <div class="section-header">
          <div>
            <h2>互动内容</h2>
            <p>当前 M4 提供一个可运行的知识点分类练习模板。</p>
          </div>
        </div>
        <div v-for="interaction in plan.content.interactions" :key="interaction.interaction_id" class="interaction-row">
          <strong>{{ interaction.title }}</strong>
          <span>{{ interaction.prompt }}</span>
          <div class="tag-row">
            <span v-for="item in interaction.items" :key="item" class="status-pill pending">{{ item }}</span>
          </div>
        </div>
      </section>

      <section class="section-card">
        <div class="section-header">
          <div>
            <h2>来源证据</h2>
            <p>来源会被写入 PPT、教案和互动内容。</p>
          </div>
        </div>
        <div v-if="plan.source_refs.length" class="source-list">
          <div v-for="ref in plan.source_refs" :key="ref.evidence_id || `${ref.source_name}-${ref.quote}`" class="source-row">
            <el-icon><CircleCheck /></el-icon>
            <span>{{ sourceLabel([ref]) }}</span>
            <small>{{ ref.quote }}</small>
          </div>
        </div>
        <el-empty v-else description="当前蓝图没有关联证据" :image-size="72" />
      </section>

      <el-alert
        v-if="task"
        :title="task.status === 'completed' ? '成果已生成，可以进入编辑页' : task.error || `生成进度 ${task.progress}%`"
        :type="task.status === 'failed' ? 'error' : task.status === 'completed' ? 'success' : 'info'"
        show-icon
        :closable="false"
      >
        <template v-if="task.status === 'completed'" #default>
          <el-button type="primary" size="small" @click="openEditor">打开成果</el-button>
        </template>
      </el-alert>
    </template>

    <el-empty v-else description="请从项目会话进入教学蓝图" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CircleCheck, MagicStick, Refresh } from '@element-plus/icons-vue'
import {
  buildCoursewarePlan,
  fetchCoursewarePlan,
  getTaskStatus,
  startProjectGeneration,
} from '@/api'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => String(route.query.projectId || localStorage.getItem('active_project_id') || ''))
const plan = ref(null)
const task = ref(null)
const loading = ref(false)
const generating = ref(false)
const errorMessage = ref('')
let pollTimer = null

function sourceLabel(refs) {
  if (!refs?.length) return '暂无'
  return refs.map((ref) => {
    const locator = ref.locator || {}
    const location = ['page', 'slide', 'timestamp', 'paragraph']
      .filter((key) => locator[key] !== undefined)
      .map((key) => `${key} ${locator[key]}`)
      .join(', ')
    return location ? `${ref.source_name} (${location})` : ref.source_name
  }).join('；')
}

async function loadPlan() {
  if (!projectId.value) {
    errorMessage.value = '缺少项目 ID，请从项目会话进入教学蓝图'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    try {
      plan.value = (await fetchCoursewarePlan(projectId.value)).data
    } catch (error) {
      if (error.response?.status !== 404) throw error
      plan.value = (await buildCoursewarePlan(projectId.value)).data
    }
    localStorage.setItem('active_project_id', projectId.value)
  } catch (error) {
    errorMessage.value = error.response?.data?.error?.message || '教学蓝图加载失败，请先确认需求'
  } finally {
    loading.value = false
  }
}

async function pollTask(taskId) {
  const startedAt = Date.now()
  const tick = async () => {
    try {
      task.value = (await getTaskStatus(taskId)).data
      if (['completed', 'failed'].includes(task.value.status)) {
        generating.value = false
        if (task.value.status === 'completed') ElMessage.success('三类成果已生成')
        return
      }
      if (Date.now() - startedAt > 120000) {
        generating.value = false
        errorMessage.value = '生成时间较长，请稍后刷新任务状态'
        return
      }
      pollTimer = window.setTimeout(tick, 1000)
    } catch (error) {
      generating.value = false
      errorMessage.value = error.response?.data?.error?.message || '生成状态获取失败'
    }
  }
  await tick()
}

async function generate() {
  if (!plan.value || generating.value) return
  generating.value = true
  errorMessage.value = ''
  try {
    task.value = (await startProjectGeneration(projectId.value, plan.value.plan_id)).data
    await pollTask(task.value.task_id)
  } catch (error) {
    generating.value = false
    errorMessage.value = error.response?.data?.error?.message || '成果生成失败，请重试'
  }
}

function openEditor() {
  router.push({
    path: '/editor',
    query: {
      projectId: projectId.value,
      taskId: task.value?.task_id,
      planId: plan.value?.plan_id,
    },
  })
}

onMounted(loadPlan)
onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer)
})
</script>

<style scoped>
.slide-list,
.source-list {
  display: grid;
  gap: 10px;
}

.slide-item {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid #e6eaf0;
}

.slide-item:last-child {
  border-bottom: 0;
}

.slide-order {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  background: #eef4ff;
  color: #1463ff;
  font-weight: 700;
}

.slide-content {
  display: grid;
  gap: 4px;
}

.slide-content span,
.slide-content small,
.interaction-row span,
.source-row small {
  color: #64748b;
}

.slide-content p {
  margin: 4px 0;
}

.interaction-row {
  display: grid;
  gap: 10px;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.source-row {
  display: grid;
  grid-template-columns: auto minmax(160px, 0.7fr) 1fr;
  gap: 10px;
  align-items: baseline;
}

@media (max-width: 900px) {
  .source-row {
    grid-template-columns: auto 1fr;
  }

  .source-row small {
    grid-column: 2;
  }
}
</style>
