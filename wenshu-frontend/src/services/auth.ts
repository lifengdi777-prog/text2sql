import axios from 'axios'

import type { AuthUser } from '@/lib/authToken'
import { getToken } from '@/lib/authToken'

// 鉴权 REST(注册 / 登录 / 查当前用户)。独立 axios 实例:
// 登录/注册无需 token;/auth/me 需要,这里用请求拦截器按需带上 Bearer。
const authApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
})

authApi.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export interface TokenResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

export async function loginRequest(username: string, password: string): Promise<TokenResponse> {
  const { data } = await authApi.post<TokenResponse>('/auth/login', { username, password })
  return data
}

export async function registerRequest(username: string, password: string): Promise<TokenResponse> {
  const { data } = await authApi.post<TokenResponse>('/auth/register', { username, password })
  return data
}

export async function fetchMe(): Promise<AuthUser> {
  const { data } = await authApi.get<AuthUser>('/auth/me')
  return data
}

// 把后端返回的错误 message 抽出来给 UI 展示。
export function toAuthError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail
    return detail || error.message || '请求失败，请稍后重试'
  }
  if (error instanceof Error) return error.message
  return '请求失败，请稍后重试'
}
