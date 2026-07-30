import request from '@/utils/request'
import type { Message } from '@/types/chat'

/** 创建会话请求参数 */
export interface CreateSessionParams {
  courseName: string
}

/** 创建会话返回 */
export interface CreateSessionResult {
  sessionId: string
}

/** 会话详情 */
export interface SessionDetail {
  sessionId: string
  courseName: string
  messages: Message[]
}

/**
 * 创建新会话
 * POST /sessions
 */
export function createSession(data: CreateSessionParams): Promise<CreateSessionResult> {
  return request.post('/sessions', data)
}

/**
 * 查询会话详情与历史消息
 * GET /sessions/{id}
 */
export function getSession(sessionId: string): Promise<SessionDetail> {
  return request.get(`/sessions/${sessionId}`)
}
