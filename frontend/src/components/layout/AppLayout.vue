<template>
  <div class="app-shell">
    <SidebarNav />

    <div class="app-body">
      <TopBar />
      <main :class="['content-surface', { 'chat-surface': route.name === 'chat' }]">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'
import SidebarNav from './SidebarNav.vue'
import TopBar from './TopBar.vue'

const route = useRoute()
</script>

<style scoped>
.app-shell {
  height: 100dvh;
  min-height: 0;
  display: flex;
  overflow: hidden;
  background: #f7f9fc;
  color: #111827;
}

.app-body {
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.content-surface {
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 28px;
  overflow: auto;
}

.content-surface.chat-surface {
  overflow: hidden;
}

@media (max-width: 980px) {
  .content-surface.chat-surface {
    overflow: auto;
  }
}

@media (max-width: 720px) {
  .content-surface {
    padding: 18px;
  }
}
</style>
