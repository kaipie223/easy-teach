<template>
  <el-card class="chat-panel">
    <template #header>💬 教学对话</template>

    <!-- 消息列表 -->
    <div class="message-list" ref="msgList">
      <div v-for="(msg, i) in store.messages" :key="i"
           :class="['message', msg.role === 'user' ? 'user' : 'assistant']">
        <div class="content">{{ msg.content }}</div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <el-input
        v-model="input"
        type="textarea"
        :rows="3"
        placeholder="描述您的教学需求，例如：'我需要一份初中物理浮力章节的新课课件...'"
        @keydown.enter.exact.prevent="send"
      />
      <el-button type="primary" @click="send" :loading="loading" style="margin-top: 8px;">
        发送
      </el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()
const input = ref('')
const loading = ref(false)
const msgList = ref(null)

async function send() {
  if (!input.value.trim()) return
  loading.value = true
  await store.sendMessage(input.value)
  input.value = ''
  loading.value = false
}
</script>

<style scoped>
.chat-panel { height: 100%; display: flex; flex-direction: column; }
.message-list { flex: 1; overflow-y: auto; padding: 10px; }
.message { margin-bottom: 12px; padding: 8px 12px; border-radius: 8px; }
.message.user { background: #ecf5ff; text-align: right; }
.message.assistant { background: #f5f7fa; }
.input-area { margin-top: 12px; }
</style>
