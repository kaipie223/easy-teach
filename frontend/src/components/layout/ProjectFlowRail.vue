<template>
  <div class="flow-rail">
    <nav class="flow-inner" aria-label="教案流程">
      <div class="flow-context">
        <span class="context-label">教案</span>
        <strong class="context-title">{{ projectTitle }}</strong>
      </div>

      <ol class="flow-steps">
        <li v-for="(step, index) in steps" :key="step.name" class="flow-step">
          <RouterLink
            class="step-link"
            :class="{ active: index === currentIndex, done: index < currentIndex }"
            :to="step.to"
            :aria-current="index === currentIndex ? 'step' : undefined"
          >
            <span class="step-dot">
              <el-icon v-if="index < currentIndex"><Check /></el-icon>
              <template v-else>{{ index + 1 }}</template>
            </span>
            <span class="step-label">{{ step.label }}</span>
          </RouterLink>
          <span
            v-if="index < steps.length - 1"
            class="step-connector"
            :class="{ done: index < currentIndex }"
            aria-hidden="true"
          />
        </li>
      </ol>

      <span class="flow-counter text-tabular">{{ currentIndex + 1 }} / {{ steps.length }}</span>
    </nav>

    <div class="flow-progress" aria-hidden="true">
      <span class="flow-progress-fill" :style="{ width: progressPercent }" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { Check } from '@element-plus/icons-vue'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const projectStore = useProjectStore()

// 教案主线：与产品流程一一对应，顺序即心流顺序
const steps = [
  { name: 'requirement', label: '需求共创', to: '/requirements', routes: ['requirements', 'chat'] },
  { name: 'materials', label: '资料', to: '/materials', routes: ['materials'] },
  { name: 'blueprint', label: '教学蓝图', to: '/blueprint', routes: ['blueprint'] },
  { name: 'editor', label: '成果', to: '/editor', routes: ['editor'] },
  { name: 'exports', label: '导出', to: '/exports', routes: ['exports'] },
]

const currentIndex = computed(() => {
  const index = steps.findIndex(step => step.routes.includes(route.name))
  return index < 0 ? 0 : index
})

const progressPercent = computed(
  () => `${Math.round(((currentIndex.value + 1) / steps.length) * 100)}%`,
)

const projectTitle = computed(() => projectStore.activeProject?.title || '未命名教案')
</script>

<style scoped>
.flow-rail {
  flex: 0 0 auto;
  position: relative;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-hairline);
}

.flow-inner {
  display: flex;
  align-items: center;
  gap: var(--space-6);
  min-height: 60px;
  padding: 0 var(--space-6);
}

/* ── 教案上下文 ───────────────────────────────────────── */
.flow-context {
  min-width: 0;
  display: flex;
  flex-direction: column;
  flex: 0 1 auto;
}

.context-label {
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

.context-title {
  max-width: 240px;
  overflow: hidden;
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 步骤 ─────────────────────────────────────────────── */
.flow-steps {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-x: auto;
  scrollbar-width: none;
}

.flow-steps::-webkit-scrollbar { display: none; }

.flow-step {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
}

.step-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 32px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-md);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  transition: background-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.step-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.step-dot {
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: var(--radius-pill);
  background: var(--neutral-100);
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  transition: background-color var(--duration-base) var(--ease-standard),
    color var(--duration-base) var(--ease-standard);
}

.step-link.done .step-dot {
  background: var(--brand-50);
  color: var(--text-brand);
}

.step-link.active {
  background: var(--bg-active);
  color: var(--text-brand);
  font-weight: var(--weight-semibold);
}

.step-link.active .step-dot {
  background: var(--brand-500);
  color: var(--text-inverse);
}

.step-connector {
  width: 24px;
  height: 1px;
  flex: 0 0 auto;
  background: var(--border-default);
}

.step-connector.done { background: var(--brand-300); }

/* ── 进度 ─────────────────────────────────────────────── */
.flow-counter {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.flow-progress {
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  background: transparent;
}

.flow-progress-fill {
  display: block;
  height: 100%;
  border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
  background: var(--gradient-brand);
  transition: width var(--duration-slow) var(--ease-standard);
}

/* ── 响应式 ───────────────────────────────────────────── */
@media (max-width: 1024px) {
  .flow-context { display: none; }
}

@media (max-width: 768px) {
  .flow-inner {
    gap: var(--space-3);
    padding: 0 var(--space-4);
  }

  .step-link { padding: 0 var(--space-1); }

  /* 窄屏只保留圆形与当前步骤文字，避免挤压成不可读 */
  .step-label { display: none; }

  .step-link.active .step-label {
    display: inline;
    font-size: var(--text-sm);
  }

  .step-connector { width: 12px; }

  .flow-counter { display: none; }
}
</style>
