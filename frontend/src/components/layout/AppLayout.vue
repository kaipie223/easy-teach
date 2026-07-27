<template>
  <div class="app-shell">
    <SidebarNav />

    <div class="app-body">
      <TopBar />
      <main class="content-surface">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup>
import SidebarNav from './SidebarNav.vue'
import TopBar from './TopBar.vue'
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  background: #f7f9fc;
  color: #111827;
}

.app-body {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.content-surface {
  flex: 1;
  min-width: 0;
  padding: 28px;
  overflow: auto;
}

@media (max-width: 720px) {
  .content-surface {
    padding: 18px;
  }
}
</style>
