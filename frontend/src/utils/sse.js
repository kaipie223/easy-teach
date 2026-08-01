/**
 * SSE 流式解析工具
 * 用于对接后端 Chat SSE 接口，解析事件流并回调给 UI 层
 *
 * SSE 事件类型：
 *   - question   → AI 向教师提问（需渲染追问卡片）
 *   - confirm    → AI 总结确认（需渲染确认面板）
 *   - text       → 普通文本（打字机逐字追加）
 *   - [DONE]     → 流结束信号
 */

export class SSEClient {
  /**
   * @param {string} url - SSE 端点 URL
   * @param {object} callbacks - 事件回调
   * @param {function} callbacks.onQuestion - 收到 question 事件
   * @param {function} callbacks.onConfirm - 收到 confirm 事件
   * @param {function} callbacks.onText - 收到 text 文本片段
   * @param {function} callbacks.onDone - 流结束
   * @param {function} callbacks.onError - 连接错误
   */
  constructor(url, callbacks = {}) {
    this.url = url
    this.callbacks = callbacks
    this.abortController = null
    this.reader = null
    this.buffer = ''
  }

  /** 发起 SSE 连接（使用 fetch + ReadableStream 解析） */
  async connect(body = {}) {
    this.abortController = new AbortController()

    try {
      const response = await fetch(this.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(body),
        signal: this.abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`SSE 连接失败: HTTP ${response.status}`)
      }

      this.reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await this.reader.read()
        if (done) break

        this.buffer += decoder.decode(value, { stream: true })
        this._parseBuffer()
      }

      // 流结束
      this.callbacks.onDone?.()
    } catch (err) {
      if (err.name !== 'AbortError') {
        this.callbacks.onError?.(err)
      }
    }
  }

  /** 断开连接 */
  disconnect() {
    this.abortController?.abort()
    this.reader?.cancel()
    this.buffer = ''
  }

  /** 解析 SSE 缓冲区，提取完整事件 */
  _parseBuffer() {
    const lines = this.buffer.split('\n')
    // 保留最后一个可能不完整的行
    this.buffer = lines.pop() || ''

    let eventType = ''
    let dataBuffer = ''

    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        const chunk = line.slice(5).trim()

        if (chunk === '[DONE]') {
          this.callbacks.onDone?.()
          return
        }

        dataBuffer += chunk
      } else if (line === '') {
        // 空行表示一个事件结束
        if (eventType && dataBuffer) {
          this._dispatch(eventType, dataBuffer)
        }
        eventType = ''
        dataBuffer = ''
      }
    }

    // 兜底：如果只有 data 没有 event 字段
    if (dataBuffer) {
      this._dispatch(eventType || 'text', dataBuffer)
    }
  }

  /** 根据事件类型分发 */
  _dispatch(eventType, rawData) {
    let parsed = rawData
    try {
      parsed = JSON.parse(rawData)
    } catch {
      // 非 JSON 文本直接当字符串
    }

    switch (eventType) {
      case 'question':
        this.callbacks.onQuestion?.(parsed)
        break
      case 'confirm':
        this.callbacks.onConfirm?.(parsed)
        break
      case 'text':
      default:
        this.callbacks.onText?.(typeof parsed === 'string' ? parsed : parsed.text || rawData)
        break
    }
  }
}

/**
 * 快捷方法：发送对话消息并接收 SSE 流
 * @param {string} sessionId - 会话 ID
 * @param {string} message - 用户消息
 * @param {object} callbacks - 同 SSEClient callbacks
 * @returns {SSEClient} 返回 client 实例，可用于 disconnect
 */
export function streamChat(sessionId, message, callbacks) {
  const client = new SSEClient(`/api/sessions/${sessionId}/chat`, callbacks)
  client.connect({ message })
  return client
}
