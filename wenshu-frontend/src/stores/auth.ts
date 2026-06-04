import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { AuthUser } from '@/lib/authToken'
import { clearAuth, getStoredUser, getToken, setStoredUser, setToken } from '@/lib/authToken'
import { fetchMe, loginRequest, registerRequest } from '@/services/auth'

// 全局登录态。token / user 启动时从 localStorage 回填,刷新页面不掉登录。
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken())
  const user = ref<AuthUser | null>(getStoredUser())

  const isAuthenticated = computed(() => !!token.value)
  // 是否管理员:仅 role==='admin' 为真;缺省/普通用户均为 false(数据源管理按钮据此显隐)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function applyToken(newToken: string, newUser: AuthUser) {
    token.value = newToken
    user.value = newUser
    setToken(newToken)
    setStoredUser(newUser)
  }

  async function login(username: string, password: string) {
    const res = await loginRequest(username, password)
    applyToken(res.access_token, res.user)
  }

  async function register(username: string, password: string) {
    const res = await registerRequest(username, password)
    applyToken(res.access_token, res.user)
  }

  function logout() {
    token.value = null
    user.value = null
    clearAuth()
  }

  // 用已存的 token 拉一次当前用户,校验 token 是否仍有效(无效则登出)。
  async function refreshMe() {
    if (!token.value) return
    try {
      const me = await fetchMe()
      user.value = me
      setStoredUser(me)
    } catch {
      logout()
    }
  }

  return { token, user, isAuthenticated, isAdmin, login, register, logout, refreshMe }
})
