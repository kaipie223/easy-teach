<template>
  <div class="empty-state surface-mesh">
    <div class="empty-inner">
      <!--
        默认用托托替代原来的 Element 图标：空态、失败、完成的语气由 state 表达，
        比"换一个图标"更直观，也让同一套形象贯穿全站。
        需要保留图标的老用法时传 :show-mascot="false"，图标照旧渲染。
      -->
      <TotoMascot v-if="showMascot" :state="state" :size="mascotSize" />
      <span v-else-if="icon" class="empty-icon" aria-hidden="true">
        <el-icon><component :is="icon" /></el-icon>
      </span>
      <h3 class="empty-title">{{ title }}</h3>
      <p v-if="description" class="empty-description">{{ description }}</p>
      <div v-if="$slots.default" class="empty-actions">
        <slot />
      </div>
    </div>
  </div>
</template>

<script setup>
import TotoMascot from './TotoMascot.vue'

defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  icon: { type: [Object, Function, String], default: null },
  /** idle / working / success / error —— 空态的语气 */
  state: { type: String, default: 'idle' },
  /** 是否用托托替代图标（默认替代） */
  showMascot: { type: Boolean, default: true },
  /**
   * 空态卡里 148px 会显得过大，默认压到 120px；
   * 需要严格按设计稿 148px 时传 :mascot-size="148"。
   */
  mascotSize: { type: Number, default: 120 },
})
</script>

<style scoped>
/* 空态是"营销层"的一部分：允许用静态 Mesh 渐变拉质感，但依然克制 */
.empty-state {
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-xl);
  background-color: var(--bg-surface);
  padding: var(--space-12) var(--space-6);
}

.empty-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 420px;
  margin: 0 auto;
}

.empty-icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  margin-bottom: var(--space-4);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
  color: var(--text-brand);
  font-size: var(--text-xl);
}

.empty-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.empty-description {
  margin-top: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.empty-actions {
  margin-top: var(--space-5);
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

@media (max-width: 480px) {
  .empty-state { padding: var(--space-8) var(--space-4); }
}
</style>
