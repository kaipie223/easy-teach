<template>
  <div class="toto-notice" :role="resolvedRole">
    <TotoMascot class="notice-toto" :state="state" :size="size" />
    <div class="notice-body">
      <p class="notice-title">{{ title }}</p>
      <p v-if="description" class="notice-description">{{ description }}</p>
      <div v-if="$slots.default" class="notice-actions">
        <slot />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

import TotoMascot from './TotoMascot.vue'

const props = defineProps({
  /** welcome / working / success / error —— 决定托托的形象与语气 */
  state: { type: String, default: 'working' },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  size: { type: Number, default: 92 },
})

/**
 * 失败要有 role="alert" 让读屏软件立刻播报；进行中/完成用 status 更合适，
 * 它不会打断用户当前正在听的内容。
 */
const resolvedRole = computed(() => (props.state === 'error' ? 'alert' : 'status'))
</script>

<style scoped>
/* 语气靠托托与文案区分，底色统一用沉静的次级区块，避免又出现"红黄蓝三色告警墙" */
.toto-notice {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-4) var(--space-5);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-surface-sunken);
}

.notice-body {
  display: grid;
  gap: var(--space-2);
  justify-items: start;
  min-width: 0;
}

.notice-title {
  margin: 0;
  color: var(--text-primary);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  line-height: var(--leading-normal);
}

.notice-description {
  margin: 0;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.notice-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-1);
}

/* 窄屏改为纵向：一个 92px 的角色横着挤会把说明压成两三行 */
@media (max-width: 640px) {
  .toto-notice {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
  }
}
</style>
