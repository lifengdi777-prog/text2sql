// 归因面板的结构化结果:后端流末 `attribution_result` 事件的 data(payload 契约见
// docs/attribution-panel-plan.md「一期」小节)。数字全部由后端纯代码算好,前端只渲染。

export interface AttributionMember {
  member: string
  target_value: number
  baseline_value: number
  change: number
  // 增幅:基准期为 0(新增成员)时为 null
  change_pct: number | null
  // 贡献度 = 成员变化 ÷ 总变化(%):正=与总变化同向(推动),负=反向变动;可能 >100
  contribution_pct: number | null
}

export interface AttributionDimension {
  name: string
  // 已按 |变化量| 降序排序
  members: AttributionMember[]
  // 该维度两条单期子查询的 SQL(「查看 SQL」用)
  target_sql?: string | null
  baseline_sql?: string | null
}

export interface AttributionPhenomenon {
  target_value: number | null
  baseline_value: number | null
  change: number | null
  change_pct: number | null
  target_period: string
  baseline_period: string
  metric: string
  scope?: string
  compare_type?: string
  description?: string
}

export interface AttributionResult {
  phenomenon: AttributionPhenomenon
  // 主维度(LLM 选的信息量最大者)排第一,面板默认展示它
  dimensions: AttributionDimension[]
  conclusion: string
}

export function isAttributionResult(data: unknown): data is AttributionResult {
  return (
    !!data &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    'phenomenon' in data &&
    'dimensions' in data &&
    Array.isArray((data as Record<string, unknown>).dimensions)
  )
}
