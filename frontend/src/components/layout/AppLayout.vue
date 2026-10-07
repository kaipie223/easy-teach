<template>
  <div class="app-shell">
    <a class="skip-link" href="#main-content">跳到主要内容</a>

    <AppTopBar />
    <ProjectFlowRail v-if="inWorkspace" />

    <main
      id="main-content"
      class="app-content"
      :class="{ 'is-flush': isChat }"
      tabindex="-1"
    >
      <div class="app-container" :class="containerClass">
        <slot />
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppTopBar from './AppTopBar.vue'
import ProjectFlowRail from './ProjectFlowRail.vue'

const route = useRoute()

// 工作间 = 具体教案的某一环节；对话页同属"需求共创"这一步
const inWorkspace = computed(
  () => Boolean(route.meta.projectScoped) || route.name === 'chat',
)

const isChat = computed(() => route.name === 'chat')

/* 容器宽度按页面性质分三档：对话/长文阅读型、文档型、工作区宽版。
   全站居中，左右留白充足 —— 这是去掉"后台平铺感"的关键。 */
const containerClass = computed(() => {
  if (isChat.value) return 'container-wide is-full-height'
  if (['materials', 'blueprint', 'editor', 'exports'].includes(route.name)) {
    return 'container-wide'
  }
  return 'container-doc'
})
</script>

<style scoped>
.app-shell {
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-page);
}

.skip-link {
  position: absolute;
  top: var(--space-2);
  left: var(--space-2);
  z-index: var(--z-toast);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  box-shadow: var(--shadow-raised);
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  transform: translateY(-200%);
  transition: transform var(--duration-base) var(--ease-standard);
}

.skip-link:focus-visible {
  transform: translateY(0);
}

.app-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--space-8) var(--space-6) var(--space-12);
}

/* 对话页自己管滚动与内边距，外壳不参与 */
.app-content.is-flush {
  padding: 0;
  overflow: hidden;
}

.app-container {
  margin: 0 auto;
}

.app-container.is-full-height {
  height: 100%;
}

@media (max-width: 768px) {
  .app-content {
    padding: var(--space-5) var(--space-4) var(--space-8);
  }
}

@media (max-width: 480px) {
  .app-content {
    padding: var(--space-4) var(--space-3) var(--space-8);
  }
}
</style>
