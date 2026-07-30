<template>
  <div :class="['message-bubble-row', role]">
    <div class="bubble-avatar">
      <el-avatar :size="32" :style="{ background: role === 'assistant' ? '#1463ff' : '#10b981' }">
        {{ role === 'assistant' ? 'AI' : '我' }}
      </el-avatar>
    </div>
    <div class="bubble-body">
      <div class="bubble-header">
        <span class="bubble-sender">{{ role === 'assistant' ? 'TeachMate AI' : '我' }}</span>
        <span class="bubble-time">{{ formatTime(timestamp) }}</span>
      </div>
      <div class="bubble-content">
        <template v-if="typing">
          <span class="typing-text">{{ content }}</span>
          <span class="cursor-blink">|</span>
        </template>
        <template v-else>
          {{ content }}
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  role: { type: String, default: 'assistant', validator: v => ['user', 'assistant'].includes(v) },
  content: { type: String, default: '' },
  timestamp: { type: [String, Date], default: () => new Date().toISOString() },
  typing: { type: Boolean, default: false },
})

function formatTime(ts) {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<style scoped>
.message-bubble-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
}

.message-bubble-row.assistant {
  flex-direction: row;
}

.message-bubble-row.user {
  flex-direction: row-reverse;
}

.bubble-avatar {
  flex: 0 0 auto;
  padding-top: 2px;
}

.bubble-body {
  max-width: 72%;
}

.bubble-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}

.bubble-sender {
  font-size: 13px;
  font-weight: 700;
  color: #374151;
}

.bubble-time {
  font-size: 12px;
  color: #9ca3af;
}

.bubble-content {
  padding: 10px 14px;
  border-radius: 8px;
  line-height: 1.65;
  font-size: 14px;
  color: #1f2937;
  word-break: break-word;
}

.assistant .bubble-content {
  background: #eef5ff;
  border-top-left-radius: 2px;
}

.user .bubble-content {
  background: #1463ff;
  color: #ffffff;
  border-top-right-radius: 2px;
}

.user .bubble-header {
  flex-direction: row-reverse;
}

.typing-text {
  white-space: pre-wrap;
}

.cursor-blink {
  animation: blink 0.8s step-end infinite;
  color: #1463ff;
  font-weight: 700;
}

@keyframes blink {
  50% { opacity: 0; }
}
</style>
