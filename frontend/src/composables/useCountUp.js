import { ref, watch } from 'vue'

/**
 * 数字滚动：让统计数字"涨"到目标值。
 *
 * 只在值真正变化时播放，且 reduced-motion 下直接给结果 —— 动效是锦上添花，
 * 不能让辅助技术用户等它。
 */
export function useCountUp(source, { duration = 700 } = {}) {
  const display = ref(0)
  const prefersReduced = typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches

  function animateTo(target, from) {
    const to = Number(target) || 0
    const start = Number(from) || 0
    if (prefersReduced || to === start) {
      display.value = to
      return
    }
    const startedAt = performance.now()
    const tick = (now) => {
      const progress = Math.min(1, (now - startedAt) / duration)
      // ease-out-cubic：起步快、收尾稳，读完数字时不会显得拖沓
      const eased = 1 - (1 - progress) ** 3
      display.value = Math.round(start + (to - start) * eased)
      if (progress < 1) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }

  watch(
    source,
    (next, previous) => animateTo(next, previous ?? 0),
    { immediate: true },
  )

  return display
}
