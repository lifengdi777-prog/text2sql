export type AgentStepStatus = 'running' | 'success' | 'error'
export type AgentResultValue = string | number | boolean | null

// data 可能是:
//   - 数组(execute_sql 的成功结果,raw rows)
//   - 对象(chart_agent 的 chart_config,带 chart_type 字段)
//   - 字符串(interpret_result 的自然语言解读)
//   - null(中间步骤或失败)
export type AgentEventData =
  | Record<string, AgentResultValue>[]
  | Record<string, unknown>
  | string
  | null

export interface AgentEvent {
  step: string
  status: AgentStepStatus
  data: AgentEventData
  finish: boolean
  guide_queries?: string[] | null
}

export interface SseChunkParseResult {
  events: AgentEvent[]
  rest: string
}

export function parseSseChunk(input: string): SseChunkParseResult {
  const segments = input.split('\n\n')
  const hasTrailingSeparator = input.endsWith('\n\n')
  const completedSegments = hasTrailingSeparator ? segments.filter(Boolean) : segments.slice(0, -1).filter(Boolean)
  const rest = hasTrailingSeparator ? '' : segments.at(-1) ?? ''

  const events = completedSegments.flatMap((segment) => {
    const payload = segment
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('')

    if (!payload) {
      return []
    }

    try {
      return [JSON.parse(payload) as AgentEvent]
    } catch {
      return []
    }
  })

  return { events, rest }
}
