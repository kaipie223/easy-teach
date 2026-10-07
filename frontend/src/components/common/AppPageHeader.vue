<template>
  <header class="page-header">
    <div class="header-text">
      <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p v-if="subtitle" class="header-subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="header-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  eyebrow: { type: String, default: '' },
})
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-6);
}

.header-text {
  min-width: 0;
}

.page-header h1 {
  margin-top: var(--space-2);
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  color: var(--text-primary);
}

.page-header .eyebrow + h1 {
  margin-top: var(--space-2);
}

.header-text > h1:first-child {
  margin-top: 0;
}

.header-subtitle {
  margin-top: var(--space-2);
  max-width: 62ch;
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.header-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--space-4);
  }

  .page-header h1 {
    font-size: var(--text-xl);
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }
}
</style>
