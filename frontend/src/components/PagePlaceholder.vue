<template>
  <section class="placeholder-page">
    <div class="placeholder-panel">
      <p class="eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
  </section>
</template>

<script setup>
defineProps({
  eyebrow: {
    type: String,
    required: true,
  },
  title: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    required: true,
  },
})
</script>

<style scoped>
.placeholder-page {
  min-height: calc(100vh - 120px);
  display: flex;
  align-items: flex-start;
}

.placeholder-panel {
  width: min(680px, 100%);
  padding: 32px;
  border: 1px solid #e6eaf0;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
}

.eyebrow {
  margin: 0 0 10px;
  color: #1463ff;
  font-size: 13px;
  font-weight: 700;
}

h1 {
  margin: 0 0 12px;
  font-size: 28px;
  line-height: 1.25;
}

p {
  margin: 0;
  color: #4b5563;
  line-height: 1.7;
}
</style>
