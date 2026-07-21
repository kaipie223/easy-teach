import { defineStore } from 'pinia'
import { ref } from 'vue'
import { sendChatMessage } from '../api'

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref('session_' + Date.now())
  const messages = ref([])

  async function sendMessage(text) {
    // 添加用户消息
    messages.value.push({ role: 'user', content: text })

    try {
      const { data } = await sendChatMessage(sessionId.value, text, messages.value)
      // 添加 AI 回复
      messages.value.push({ role: 'assistant', content: data.reply })
      return data
    } catch (e) {
      messages.value.push({ role: 'assistant', content: '抱歉，网络错误，请重试。' })
    }
  }

  return { sessionId, messages, sendMessage }
})
