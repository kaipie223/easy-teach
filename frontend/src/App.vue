<template>
  <AppLayout v-if="!route.meta.public">
    <RouterView />
  </AppLayout>
  <RouterView v-else />
</template>

<script setup>
import { RouterView } from 'vue-router'
import { useRoute } from 'vue-router'
import AppLayout from './components/layout/AppLayout.vue'

const route = useRoute()
</script>

<style>
:root {
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
  color: #111827;
  background: #f7f9fc;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
  height: 100dvh;
  overflow: hidden;
}

#app {
  height: 100%;
}

button,
input,
textarea,
select {
  font: inherit;
}
</style>
