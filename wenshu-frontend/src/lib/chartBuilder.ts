// 前端本地构图:从原始 rows + 字段映射,确定性地构造各类型的 ECharts option。
// 用户切换图表类型时本地重渲,不回后端、不调 LLM。逻辑对应后端被删掉的模板。
import type { ChartConfig, ChartType, ResultRow } from '@/types/agent'

export interface FieldMap {
  dimension?: string
  measure?: string
  series?: string
}

function num(v: unknown): number {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : 0
}

// 全是数字按数值排,否则按字符串排
function uniqueSorted(values: unknown[]): unknown[] {
  const uniq = Array.from(new Set(values))
  const allNum = uniq.every(
    (v) => typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))),
  )
  return allNum
    ? uniq.sort((a, b) => Number(a) - Number(b))
    : uniq.sort((a, b) => String(a).localeCompare(String(b)))
}

export function buildChartOption(
  type: ChartType,
  rows: ResultRow[],
  fm: FieldMap,
  title: string,
): ChartConfig {
  const dim = fm.dimension ?? ''
  const measure = fm.measure ?? ''
  const seriesField = fm.series ?? ''
  const titleObj = { text: title, left: 'center' }

  // 饼图:按数值降序,data 为 [{name, value}]
  if (type === 'pie') {
    const sorted = [...rows].sort((a, b) => num(b[measure]) - num(a[measure]))
    return {
      chart_type: 'pie',
      title: titleObj,
      tooltip: { trigger: 'item', formatter: '{b}<br/>{c} ({d}%)' },
      legend: { orient: 'vertical', left: 'left', top: 'middle', type: 'scroll' },
      series: [
        {
          name: title,
          type: 'pie',
          radius: ['38%', '68%'],
          center: ['58%', '52%'],
          avoidLabelOverlap: true,
          itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
          label: { show: true, formatter: '{b}\n{d}%' },
          data: sorted.map((r) => ({ name: String(r[dim] ?? ''), value: num(r[measure]) })),
        },
      ],
    } as ChartConfig
  }

  // 柱状图:按数值降序(排行)
  if (type === 'bar') {
    const sorted = [...rows].sort((a, b) => num(b[measure]) - num(a[measure]))
    const x = sorted.map((r) => r[dim])
    return {
      chart_type: 'bar',
      title: titleObj,
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: x, name: dim, axisLabel: { interval: 0, rotate: x.length > 6 ? 30 : 0 } },
      yAxis: { type: 'value', name: measure },
      series: [{ name: measure, type: 'bar', data: sorted.map((r) => num(r[measure])) }],
    } as ChartConfig
  }

  // 折线图:按维度(时间)升序
  if (type === 'line') {
    const sorted = [...rows].sort((a, b) => {
      const av = a[dim]
      const bv = b[dim]
      if (typeof av === 'number' && typeof bv === 'number') return av - bv
      return String(av).localeCompare(String(bv))
    })
    return {
      chart_type: 'line',
      title: titleObj,
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: sorted.map((r) => r[dim]), name: dim },
      yAxis: { type: 'value', name: measure },
      series: [{ name: measure, type: 'line', smooth: true, data: sorted.map((r) => num(r[measure])) }],
    } as ChartConfig
  }

  // 多线 / 堆叠柱:长表透视成宽表
  if (type === 'multi_line' || type === 'stacked_bar') {
    const isStacked = type === 'stacked_bar'
    const xVals = uniqueSorted(
      rows.map((r) => r[dim]).filter((v) => v !== null && v !== undefined),
    )
    const seriesNames = Array.from(
      new Set(rows.map((r) => String(r[seriesField] ?? '')).filter((v) => v !== '')),
    )
    const lookup = new Map<string, number>()
    for (const r of rows) lookup.set(`${r[dim]}||${r[seriesField]}`, num(r[measure]))

    const seriesList = seriesNames.map((s) => ({
      name: s,
      type: isStacked ? 'bar' : 'line',
      ...(isStacked ? { stack: 'total' } : { smooth: true }),
      // 缺失值:折线用 null(断开),堆叠用 0
      data: xVals.map((x) => {
        const key = `${x}||${s}`
        return lookup.has(key) ? lookup.get(key)! : isStacked ? 0 : null
      }),
    }))

    return {
      chart_type: type,
      title: titleObj,
      tooltip: { trigger: 'axis', ...(isStacked ? { axisPointer: { type: 'shadow' } } : {}) },
      legend: { data: seriesNames, top: 'bottom', type: 'scroll' },
      grid: { left: '3%', right: '4%', bottom: '15%', containLabel: true },
      xAxis: { type: 'category', data: xVals, name: dim, ...(isStacked ? {} : { boundaryGap: false }) },
      yAxis: { type: 'value' },
      series: seriesList,
    } as ChartConfig
  }

  // 兜底:table 标记(由 ChartPanel 改用表格渲染)
  return { chart_type: 'table', title: titleObj }
}
