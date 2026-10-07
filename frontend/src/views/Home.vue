<template>
  <div class="home-page">
    <!-- Stripe 式动态极光：色彩集中在右上，文字一侧由白纱罩住 -->
    <AuroraBackdrop class="home-backdrop" />

    <div class="home-content">
      <AppPageHeader
        title="新建教案"
        subtitle="只要一句课程名称，剩下的我们一起捋。"
      />

      <section class="section-card create-card">
        <div class="card-top">
          <div class="card-intro">
            <span class="card-mark" aria-hidden="true">
              <el-icon><EditPen /></el-icon>
            </span>

            <h2>这节课你要讲什么？</h2>
            <p class="card-subtitle">写下课题，我会先陪你确认学段、课时与重难点。</p>
          </div>

          <!-- 首次进入是"准备开始"的语气：托托在这里打招呼 -->
          <TotoMascot class="card-toto" state="welcome" :size="148" />
        </div>

        <ul class="step-list">
          <li v-for="item in steps" :key="item">
            <el-icon class="step-icon"><Select /></el-icon>
            <span>{{ item }}</span>
          </li>
        </ul>

        <form class="create-form" @submit.prevent="handleCreate">
          <el-input
            v-model="courseName"
            size="large"
            placeholder="例如：Python 入门、初中物理力学、显性遗传……"
            :disabled="loading"
            aria-label="课程名称"
          >
            <template #prefix>
              <el-icon><Reading /></el-icon>
            </template>
          </el-input>
          <el-button
            type="primary"
            size="large"
            native-type="submit"
            :loading="loading"
            :disabled="!courseName.trim()"
          >
            开始创建
          </el-button>
        </form>

        <p class="form-hint">按 Enter 也可以直接开始。</p>

        <el-alert
          v-if="errorMsg"
          class="create-error"
          :title="errorMsg"
          type="error"
          show-icon
          :closable="false"
        />
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { EditPen, Reading, Select } from '@element-plus/icons-vue'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
import { createProject } from '@/api'
import AppPageHeader from '@/components/common/AppPageHeader.vue'
import AuroraBackdrop from '@/components/common/AuroraBackdrop.vue'
import TotoMascot from '@/components/common/TotoMascot.vue'

const router = useRouter()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const courseName = ref('')
const loading = ref(false)
const errorMsg = ref('')
const createdProject = ref(null)

const steps = ['确认学段、课时与重难点', '按需上传参考资料', '生成课件、教案与互动练习']

async function handleCreate() {
  const name = courseName.value.trim()
  if (!name || loading.value) return

  loading.value = true
  errorMsg.value = ''

  try {
    if (!createdProject.value) {
      const response = await createProject({ title: name, scenario: '' })
      createdProject.value = response.data
      projectStore.selectProject(response.data)
    }
    const res = await sessionStore.createSession(name, createdProject.value.project_id)
    projectStore.setActiveSession(res.session_id)
    router.push(`/chat/${res.session_id}`)
  } catch (err) {
    errorMsg.value = err.response?.data?.error?.message || err.message || '创建失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.home-page {
  position: relative;
  min-height: 100%;
}

/* 全屏铺满视口：fixed 保证内容再短也不会在下方露白。
   层级保持 auto：顶栏已自带 z-index，内容在后自然盖住背景。 */
.home-backdrop {
  position: fixed;
  inset: 0;
  pointer-events: none;
}

.home-content {
  position: relative;
  max-width: 620px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.create-card {
  display: flex;
  flex-direction: column;
  padding: var(--space-8);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-overlay);
}

/* 卡片顶部：左边说明、右边托托；窄屏改成上下，角色居中不挤压文字 */
.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
}

.card-intro { min-width: 0; }

.card-toto { flex: none; }

@media (max-width: 640px) {
  .card-top {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-4);
  }

  .card-toto { align-self: center; }
}

.card-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  margin-bottom: var(--space-4);
  border-radius: var(--radius-lg);
  background: var(--brand-50);
  color: var(--text-brand);
  font-size: var(--text-lg);
}

.create-card h2 {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.card-subtitle {
  margin-top: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.step-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-5) 0 0;
  padding: 0;
  list-style: none;
}

.step-list li {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--text-secondary);
  font-size: var(--text-sm);
}

.step-icon {
  color: var(--text-brand);
  font-size: var(--text-base);
}

.create-form {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-6);
}

.create-form :deep(.el-input) { flex: 1; }

.form-hint {
  margin-top: var(--space-3);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.create-error { margin-top: var(--space-4); }

@media (max-width: 768px) {
  .create-card { padding: var(--space-6) var(--space-5); }

  .create-form {
    flex-direction: column;
  }

  .create-form :deep(.el-button) { width: 100%; }
}
</style>
