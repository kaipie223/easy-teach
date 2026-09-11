import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ACTIVE_PROJECT_KEY, fetchProject, fetchProjects } from '@/api'

function storedProjectId() {
  return localStorage.getItem(ACTIVE_PROJECT_KEY) || ''
}

export const useProjectStore = defineStore('project', () => {
  const projects = ref([])
  const activeProjectId = ref(storedProjectId())
  const activeProject = ref(null)
  const loading = ref(false)

  const activeSessionId = computed(() => activeProject.value?.session_id || '')
  const hasActiveProject = computed(() => Boolean(activeProjectId.value))

  function selectProject(project) {
    if (!project?.project_id || project.status === 'deleted') {
      clearActiveProject()
      return
    }
    activeProjectId.value = project.project_id
    activeProject.value = { ...project }
    localStorage.setItem(ACTIVE_PROJECT_KEY, project.project_id)
  }

  function setActiveSession(sessionId) {
    if (!activeProject.value || !sessionId) return
    activeProject.value = { ...activeProject.value, session_id: sessionId }
  }

  function updateActiveProject(changes) {
    if (!activeProject.value) return
    activeProject.value = { ...activeProject.value, ...changes }
  }

  function clearActiveProject() {
    activeProjectId.value = ''
    activeProject.value = null
    localStorage.removeItem(ACTIVE_PROJECT_KEY)
  }

  async function selectProjectById(projectId) {
    if (!projectId) {
      clearActiveProject()
      return null
    }
    if (activeProject.value?.project_id === projectId) return activeProject.value
    try {
      const { data } = await fetchProject(projectId)
      selectProject(data)
      return data
    } catch (error) {
      if ([403, 404].includes(error.response?.status)) clearActiveProject()
      throw error
    }
  }

  async function ensureActiveProject() {
    if (!activeProjectId.value) return null
    if (activeProject.value?.project_id === activeProjectId.value) return activeProject.value
    return selectProjectById(activeProjectId.value)
  }

  async function loadProjects(includeDeleted = false) {
    loading.value = true
    try {
      const { data } = await fetchProjects(includeDeleted)
      projects.value = data || []
      const selected = projects.value.find(item => item.project_id === activeProjectId.value)
      if (selected?.status === 'deleted') clearActiveProject()
      else if (selected) selectProject(selected)
      return projects.value
    } finally {
      loading.value = false
    }
  }

  return {
    projects,
    activeProjectId,
    activeProject,
    activeSessionId,
    hasActiveProject,
    loading,
    selectProject,
    selectProjectById,
    setActiveSession,
    updateActiveProject,
    clearActiveProject,
    ensureActiveProject,
    loadProjects,
  }
})
