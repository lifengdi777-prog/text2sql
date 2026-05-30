export type StepStatus = 'running' | 'success' | 'error'

export interface AgentStep {
  step: string
  status: StepStatus
}

export interface ResultRow {
  [key: string]: string | number | boolean | null
}

// chart_agent 子图产出的统一图表配置
// 9 种 chart_type:line / multi_line / bar / stacked_bar / pie / table / metric / empty / error
export type ChartType =
  | 'line'
  | 'multi_line'
  | 'bar'
  | 'stacked_bar'
  | 'pie'
  | 'table'
  | 'metric'
  | 'empty'
  | 'error'

// 通用结构:所有 chart_type 都至少有 chart_type 字段,其余按类型扩展
export interface ChartConfig {
  chart_type: ChartType
  title?: string | { text: string; left?: string }
  // 后端确定性算出的兼容类型集 + 字段映射,供前端切换菜单 + 本地构图
  compatible_types?: ChartType[]
  field_map?: { dimension?: string; measure?: string; measures?: string[]; series?: string }
  // metric 卡专用
  metrics?: Array<{ label: string; value: string | number | null; unit?: string }>
  // error 卡专用
  message?: string
  hint?: string
  original_sql?: string | null
  // 6 种正常图表 / table 的字段由前端按 chart_type 分别解析,这里不强制
  [key: string]: unknown
}

export interface AgentReplyMessage {
  id: string
  role: 'assistant'
  steps: AgentStep[]
  result: ResultRow[]
  chartConfig: ChartConfig | null   // 由 chart_agent 子图最后一个 finish 事件填充
  interpretation: string | null     // 由 interpret_result 节点产出的自然语言解读
  sql: string | null                // 真正执行的那条 SQL(执行成功事件带上),供「查看 SQL」展示
  guideQueries: string[]
  status: 'streaming' | 'success' | 'error'
  errorMessage?: string
}

export interface UserMessage {
  id: string
  role: 'user'
  content: string
}

export type ChatMessage = UserMessage | AgentReplyMessage

// 流式查询函数签名:DW 问答和数据集问答都符合,ChatConsole 据此通用化
export type StreamFn = (
  query: string,
  options: {
    signal?: AbortSignal
    onStep: (message: AgentReplyMessage) => void
    conversationId?: number | null
    onConversation?: (id: number) => void
  },
) => Promise<void>
