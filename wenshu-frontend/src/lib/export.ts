// 结果导出工具:把查询结果行导出成 CSV 下载。
// 纯前端、零依赖。导出的是「当前展示的行」(后端已封顶 1000 行)。
import type { ResultRow } from '@/types/agent'

// 单个单元格转 CSV 字段:null/undefined 留空;含 , " 换行 的值用双引号包裹,内部 " 转义成 ""。
function toCsvField(value: unknown): string {
  if (value === null || value === undefined) return ''
  const s = String(value)
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`
  }
  return s
}

// 把行数组转成 CSV 文本(列取第一行的 key 顺序)。无数据返回空串。
export function rowsToCsv(rows: ResultRow[]): string {
  const first = rows[0]
  if (!first) return ''
  const columns = Object.keys(first)
  const header = columns.map(toCsvField).join(',')
  const body = rows.map((row) => columns.map((c) => toCsvField(row[c])).join(',')).join('\r\n')
  return `${header}\r\n${body}`
}

// 触发浏览器下载。加 UTF-8 BOM(﻿),保证 Excel 打开中文不乱码。
export function exportRowsToCsv(rows: ResultRow[], filename = 'query_result.csv'): void {
  const csv = rowsToCsv(rows)
  if (!csv) return
  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
