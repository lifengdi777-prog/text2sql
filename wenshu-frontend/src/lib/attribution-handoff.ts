// 归因请求在「聊天页 → 新开的归因页」之间的交接。
//
// 归因在全新的浏览器标签页里进行(独立 web 页面,不占用聊天),但结果行可能很大,
// 塞不进 URL → 写 localStorage(同源新标签页可读),URL 只带一个 id。
// 条目保留(不读后即删):归因页 F5 刷新可凭 id 重跑(子查询走后端 SQL 缓存,代价低);
// 每次新发起时顺手清理过期条目,避免 localStorage 越积越多。
import type { AttributionRequest } from '@/services/agent'
import { uuid } from '@/lib/uuid'

const PREFIX = 'wenshu.attribution.'
// 条目保留 24 小时:够"过会儿刷新看看",又不至于长期占空间
const TTL_MS = 24 * 60 * 60 * 1000

interface StoredEntry {
  ts: number
  req: AttributionRequest
}

function prune(): void {
  try {
    const now = Date.now()
    const stale: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (!key || !key.startsWith(PREFIX)) continue
      try {
        const entry = JSON.parse(localStorage.getItem(key) ?? '') as StoredEntry
        if (!entry?.ts || now - entry.ts > TTL_MS) stale.push(key)
      } catch {
        stale.push(key)
      }
    }
    stale.forEach((key) => localStorage.removeItem(key))
  } catch {
    /* localStorage 不可用时静默 */
  }
}

export function stashAttributionRequest(req: AttributionRequest): string {
  prune()
  const id = uuid()
  localStorage.setItem(`${PREFIX}${id}`, JSON.stringify({ ts: Date.now(), req } satisfies StoredEntry))
  return id
}

export function loadAttributionRequest(id: string): AttributionRequest | null {
  try {
    const raw = localStorage.getItem(`${PREFIX}${id}`)
    if (!raw) return null
    const entry = JSON.parse(raw) as StoredEntry
    return entry?.req ?? null
  } catch {
    return null
  }
}

// 归因页内切换口径后回写,让 F5 刷新保留最后选的口径
export function restashAttributionRequest(id: string, req: AttributionRequest): void {
  try {
    localStorage.setItem(`${PREFIX}${id}`, JSON.stringify({ ts: Date.now(), req } satisfies StoredEntry))
  } catch {
    /* 静默 */
  }
}
