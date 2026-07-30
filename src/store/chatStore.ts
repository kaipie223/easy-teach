import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 课件章节大纲 */
export interface ChapterOutline {
  title: string
  detail: string
}

/** 生成状态 */
export type GenerationStatus = 'idle' | 'generating' | 'success'

export const useChatStore = defineStore('chat', () => {
  /** 当前生成的课件大纲 */
  const currentOutline = ref<ChapterOutline[]>([])

  /** 课程名称 */
  const courseName = ref('')

  /** 生成状态 */
  const generationStatus = ref<GenerationStatus>('idle')

  /** 更新课程名称 */
  function setCourseName(name: string) {
    courseName.value = name
  }

  /** 更新大纲数据 */
  function updateOutline(outline: ChapterOutline[]) {
    currentOutline.value = outline
  }

  /** 改变生成状态 */
  function setGenerationStatus(status: GenerationStatus) {
    generationStatus.value = status
  }

  /** 重置 store 到初始状态 */
  function resetStore() {
    currentOutline.value = []
    courseName.value = ''
    generationStatus.value = 'idle'
  }

  return {
    currentOutline,
    courseName,
    generationStatus,
    setCourseName,
    updateOutline,
    setGenerationStatus,
    resetStore
  }
})
