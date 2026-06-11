// 从查询结果行里识别"期间候选"(归因弹层的观察期选择用)。
//
// 多期结果(如"2026年各月份的实际产量")做归因时,观察期必须由用户选,
// 不能让系统暗中挑"最近一期" —— 这里负责找出时间列并给出候选(倒序,最近在前)。
// 纯启发式:识别不出就返回空数组,弹层不显示观察期选择,后端按兜底规则处理。
import type { ResultRow } from '@/types/agent'

// 列名特征:月份/日期/季度/年份/时间…
const NAME_RE = /日期|月份|年月|季度|年份|时间|date|month|quarter|year|time|period/i

// 取值特征:常见期间写法(全部命中才算时间列,避免把普通编号列误判成期间)
const VALUE_RES = [
  /^\d{4}[-/年]\s?\d{1,2}([-/月]\d{1,2}日?)?$/, // 2026-03 / 2026/3/15 / 2026年3月(15日)
  /^\d{4}[-年]?\s?Q[1-4]$/i,                    // 2026Q1 / 2026年Q1 / 2026-Q1
  /^Q[1-4]$/i,                                  // Q1
  /^\d{1,2}月$/,                                // 3月
  /^\d{4}年?$/,                                 // 2026 / 2026年
  /^\d{4}-\d{2}-\d{2}[T ]/,                     // ISO 日期时间
]

// 候选上限:按日分组一年有 365 个,下拉太长没有意义,只留最近的
const MAX_CANDIDATES = 60

function isPeriodValue(v: string): boolean {
  return VALUE_RES.some((re) => re.test(v.trim()))
}

// 倒序排序键:取值里的数字序列(["2026年10月"]→[2026,10]),逐位比较;
// 字符串倒序对 "10月" vs "9月" 这类会错,数字序列不会
function numericKey(v: string): number[] {
  return (v.match(/\d+/g) ?? []).map(Number)
}

function compareDesc(a: string, b: string): number {
  const ka = numericKey(a)
  const kb = numericKey(b)
  const n = Math.max(ka.length, kb.length)
  for (let i = 0; i < n; i++) {
    const d = (kb[i] ?? -1) - (ka[i] ?? -1)
    if (d !== 0) return d
  }
  return 0
}

export function detectPeriodCandidates(rows: ResultRow[]): string[] {
  const first = rows[0]
  if (!first) return []

  let bestValues: string[] | null = null
  let bestScore = 0
  for (const key of Object.keys(first)) {
    const values = rows
      .map((r) => r[key])
      .filter((v) => v !== null && v !== undefined)
      .map(String)
    if (values.length === 0) continue
    const distinct = [...new Set(values)]
    if (distinct.length < 2) continue // 单期/常量列:不需要选

    // 取值全像期间 = 2 分,列名像时间列 = 1 分;同分取先出现的列
    const score = (values.every(isPeriodValue) ? 2 : 0) + (NAME_RE.test(key) ? 1 : 0)
    if (score > bestScore) {
      bestScore = score
      bestValues = distinct
    }
  }

  if (!bestValues) return []
  return bestValues.sort(compareDesc).slice(0, MAX_CANDIDATES)
}
