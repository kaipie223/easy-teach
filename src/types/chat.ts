/** 消息角色类型 */
export type RoleType = 'user' | 'assistant' | 'system'

/** 事件类型 */
export type EventType = 'text' | 'question' | 'confirm' | 'done'

/** 追问卡片数据 */
export interface QuestionData {
  /** 追问标题 */
  title: string
  /** 选项列表 */
  options: { label: string; value: string }[]
}

/** 确认面板数据 */
export interface ConfirmData {
  /** 课程名称 */
  courseName: string
  /** 目标受众 */
  targetAudience: string
  /** 章节列表 */
  chapters: { title: string; detail: string }[]
}

/** 完整消息体 */
export interface Message {
  /** 消息唯一标识 */
  id: string
  /** 角色类型 */
  role: RoleType
  /** 事件类型 */
  eventType: EventType
  /** 消息文本内容 */
  content: string
  /** 追问数据（eventType 为 question 时存在） */
  questionData?: QuestionData
  /** 确认面板数据（eventType 为 confirm 时存在） */
  confirmData?: ConfirmData
  /** 时间戳（毫秒） */
  timestamp: number
}
