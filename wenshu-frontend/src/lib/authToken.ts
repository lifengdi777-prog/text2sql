// 登录态的本地持久化(方案 B,JWT)。
//
// access_token 存 localStorage,所有请求经 axios 拦截器带上 Authorization: Bearer。
// 同时缓存用户信息,刷新页面后无需先请求 /auth/me 即可回填顶栏用户名。
//
// 这里只做存取,不含任何业务逻辑,供 axios 拦截器 / Pinia store / 路由守卫共用。
const TOKEN_KEY = 'wenshu.token'
const USER_KEY = 'wenshu.user'

export interface AuthUser {
  id: number
  username: string
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* localStorage 不可用(隐私模式)时静默降级 */
  }
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function setStoredUser(user: AuthUser): void {
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    /* 同上 */
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    /* 同上 */
  }
}

// token 失效(后端返回 401)时调用:清登录态并跳回登录页,带 redirect 方便回跳。
// 用整页跳转而非 router.push,顺带把内存里的所有状态(含 Pinia)重置干净。
export function redirectToLogin(): void {
  clearAuth()
  if (window.location.pathname !== '/login') {
    const back = window.location.pathname + window.location.search
    window.location.href = `/login?redirect=${encodeURIComponent(back)}`
  }
}
