// 归因请求在「聊天页 → 新开的归因页」之间的交接,以及归因结果的本地留存。
//
// 归因在全新的浏览器标签页里进行(独立 web 页面,不占用聊天),但结果行可能很大,
// 塞不进 URL → 写 localStorage(同源新标签页可读),URL 只带一个 id。
// 条目除请求体外还存**各口径/观察期的运行快照**(results):
//   - 归因页里同比⇄环比来回切,命中快照直接回放,不重新计算;
//   - F5 刷新命中快照也直接回放(没有快照才重跑,子查询走后端 SQL 缓存,代价低)。
// 条目保留 24 小时;每次新发起时顺手清理过期条目,避免 localStorage 越积越多。
import type { AttributionRequest } from '@/services/agent'
import type { AttributionSnapshot } from '@/types/attribution'
import { uuid } from '@/lib/uuid'

const PREFIX = 'wenshu.attribution.'
// 条目保留 24 小时:够"过会儿刷新看看",又不至于长期占空间
const TTL_MS = 24 * 60 * 60 * 1000

export interface AttributionEntry {
  req: AttributionRequest
  // 运行快照缓存,键 = `${compareType}|${targetPeriod ?? ''}`
  results?: Record<string, AttributionSnapshot>
}

interface StoredEntry extends AttributionEntry {
  ts: number
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
  saveAttributionEntry(id, { req })
  return id
}

export function loadAttributionEntry(id: string): AttributionEntry | null {
  try {
    const raw = localStorage.getItem(`${PREFIX}${id}`)
    if (!raw) return null
    const entry = JSON.parse(raw) as StoredEntry
    return entry?.req ? { req: entry.req, results: entry.results } : null
  } catch {
    return null
  }
}

// 归因页内回写:切口径(req 变化)/运行结束(results 增量)都保存,F5 原样恢复
export function saveAttributionEntry(id: string, entry: AttributionEntry): void {
  try {
    localStorage.setItem(
      `${PREFIX}${id}`,
      JSON.stringify({ ts: Date.now(), ...entry } satisfies StoredEntry),
    )
  } catch {
    // 容量不足(结果行太大)时退化:只存请求,不存快照,行为回到"切换/刷新重跑"
    try {
      localStorage.setItem(
        `${PREFIX}${id}`,
        JSON.stringify({ ts: Date.now(), req: entry.req } satisfies StoredEntry),
      )
    } catch {
      /* 静默 */
    }
  }
}
