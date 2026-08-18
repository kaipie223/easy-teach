import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  clearAuthSession,
  fetchCurrentUser,
  getAccessToken,
  loginAccount,
  registerAccount,
  saveAuthSession,
  USER_KEY,
} from '@/api'

function readStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getAccessToken())
  const user = ref(readStoredUser())
  const loading = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value))
  const displayName = computed(() => user.value?.display_name || user.value?.email || '未登录')

  function applyAuth(payload) {
    saveAuthSession(payload)
    token.value = payload.access_token
    user.value = payload.user
  }

  async function login(payload) {
    loading.value = true
    try {
      const { data } = await loginAccount(payload)
      applyAuth(data)
      return data
    } finally {
      loading.value = false
    }
  }

  async function register(payload) {
    loading.value = true
    try {
      const { data } = await registerAccount(payload)
      applyAuth(data)
      return data
    } finally {
      loading.value = false
    }
  }

  async function refreshUser() {
    if (!getAccessToken()) return null
    const { data } = await fetchCurrentUser()
    user.value = data
    localStorage.setItem(USER_KEY, JSON.stringify(data))
    return data
  }

  function logout() {
    clearAuthSession()
    token.value = null
    user.value = null
  }

  return {
    token,
    user,
    loading,
    isAuthenticated,
    displayName,
    login,
    register,
    refreshUser,
    logout,
  }
})
