// 过渡期匿名身份(配合后端 api/deps.py::get_current_user)。
//
// 还没有登录系统:浏览器首次访问时生成一个持久化 UUID 存进 localStorage,
// 之后所有请求都带上 X-Client-Id 头,后端据此隔离不同浏览器的数据集。
//
// ⚠️ 这不是登录、可被伪造 —— 仅用于数据隔离 + 给将来接入真鉴权(JWT/SSO)占位。
const STORAGE_KEY = 'wenshu.clientId'

export function getClientId(): string {
  try {
    const existing = localStorage.getItem(STORAGE_KEY)
    if (existing) {
      return existing
    }
    const id = crypto.randomUUID()
    localStorage.setItem(STORAGE_KEY, id)
    return id
  } catch {
    // localStorage 不可用(隐私模式 / SSR 等)→ 退化成会话内临时 id,不持久化
    return crypto.randomUUID()
  }
}
