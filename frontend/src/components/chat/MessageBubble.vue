<template>
  <div :class="['message-bubble-row', role]">
    <div class="bubble-avatar" aria-hidden="true">
      <span class="avatar-mark">{{ role === 'assistant' ? 'ET' : '我' }}</span>
    </div>
    <div class="bubble-body">
      <div class="bubble-header">
        <span class="bubble-sender">{{ role === 'assistant' ? 'easy-teach' : '我' }}</span>
        <span class="bubble-time text-tabular">{{ formatTime(timestamp) }}</span>
      </div>
      <div class="bubble-content">
        <template v-if="typing">
          <span class="typing-text">{{ content }}</span>
          <span class="cursor-blink" aria-hidden="true">|</span>
        </template>
        <template v-else>{{ content }}</template>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  role: { type: String, default: 'assistant', validator: v => ['user', 'assistant'].includes(v) },
  content: { type: String, default: '' },
  timestamp: { type: [String, Date], default: () => new Date().toISOString() },
  typing: { type: Boolean, default: false },
})

function formatTime(ts) {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<style scoped>
.message-bubble-row {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
}

.message-bubble-row.user { flex-direction: row-reverse; }

.avatar-mark {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
}

.assistant .avatar-mark {
  background: var(--gradient-brand);
  color: var(--text-inverse);
}

.user .avatar-mark {
  background: var(--neutral-150);
  color: var(--text-secondary);
}

.bubble-body {
  min-width: 0;
  max-width: 78%;
}

.bubble-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
}

.user .bubble-header { flex-direction: row-reverse; }

.bubble-sender {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  color: var(--text-secondary);
}

.bubble-time {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}

.bubble-content {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  font-size: var(--text-base);
  line-height: var(--leading-relaxed);
  word-break: break-word;
}

/* 助手：直接落在白色会话面板上，不再套一层白气泡（那会和背景糊在一起）；
   用户：主色实底气泡，形成明确的"我说 / 它说"对比。 */
.assistant .bubble-content {
  padding: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
  color: var(--text-primary);
}

.user .bubble-content {
  background: var(--brand-500);
  color: var(--text-inverse);
  border-top-right-radius: var(--radius-xs);
}

.typing-text { white-space: pre-wrap; }

.cursor-blink {
  animation: blink 0.8s step-end infinite;
  color: var(--text-brand);
  font-weight: var(--weight-bold);
}

.user .cursor-blink { color: var(--text-inverse); }

@keyframes blink {
  50% { opacity: 0; }
}

@media (max-width: 480px) {
  .bubble-body { max-width: 88%; }
}
</style>
