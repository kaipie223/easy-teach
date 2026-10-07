<template>
  <!-- 页面切换统一走淡入上浮，退场更快：有空间连续性，但绝不拖慢操作 -->
  <RouterView v-slot="{ Component, route: current }">
    <AppLayout v-if="!current.meta.public">
      <Transition name="page" mode="out-in">
        <component :is="Component" :key="current.path" />
      </Transition>
    </AppLayout>
    <Transition v-else name="page" mode="out-in">
      <component :is="Component" :key="current.path" />
    </Transition>
  </RouterView>

  <BrandIntro v-if="showIntro" @closed="showIntro = false" />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AppLayout from './components/layout/AppLayout.vue'
import BrandIntro from './components/common/BrandIntro.vue'
import { getAccessToken } from './api'

const route = useRoute()
const showIntro = ref(false)

const INTRO_SEEN_KEY = 'easy_teach_intro_seen'

/**
 * 开场动画只在"未登录 + 首次访问"时播放一次（按浏览会话计）。
 *
 * 注意不要依赖 route.meta：挂载这一刻路由还没解析完，meta 是空的，
 * 判断会永远为 false（开场就再也不会出现）。开场是应用级的，与具体路由无关。
 * reduced-motion 用户直接跳过：自动播放的视频对他们不友好。
 */
function shouldPlayIntro() {
  if (getAccessToken()) return false
  if (sessionStorage.getItem(INTRO_SEEN_KEY)) return false
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false
  return true
}

onMounted(() => {
  if (!shouldPlayIntro()) return
  // 立刻打标记：视频播到一半刷新页面不该再看一遍
  sessionStorage.setItem(INTRO_SEEN_KEY, '1')
  showIntro.value = true
})
</script>
