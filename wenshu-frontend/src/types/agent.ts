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
  // 后端附带的用户提示(如「点名的图型画不出:原因 + 可生成的图型」),横幅展示
  notice?: string | null
  original_sql?: string | null
  // 产生这份数据的完整原始问题(元字段)。图表指令轮("生成折线图")的标题/报告
  // 都需要数据源头的问题,且标题有去前缀+截断,只有这个字段是全保真的
  source_question?: string | null
  // 6 种正常图表 / table 的字段由前端按 chart_type 分别解析,这里不强制
  [key: string]: unknown
}

export interface AgentReplyMessage {
  id: string
  // 后端 messages 表的自增 id(流末事件或历史加载时填)。按需出图后据它把 chart_config 回写落库;
  // 直播态新消息在拿到流末事件前为空。
  dbId?: number
  role: 'assistant'
  steps: AgentStep[]
  result: ResultRow[]
  chartConfig: ChartConfig | null   // 由 chart_agent 子图最后一个 finish 事件填充
  interpretation: string | null     // 由 interpret_result 节点产出的自然语言解读
  sql: string | null                // 真正执行的那条 SQL(执行成功事件带上),供「查看 SQL」展示
  guideQueries: string[]
  // 意图节点改写后的自包含问题(多轮指代消解结果,如"2025年呢"→"2025年第一季度各工厂的实际产量")。
  // 按需出图/报告的标题与上下文优先用它,不能用原始残句
  standaloneQuestion?: string | null
  // 扇出风险:为 true 时前端用"警告图标 + 危险色"渲染引导区,fanoutMessage 是风险说明文案
  fanout?: boolean
  fanoutMessage?: string | null
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
    // 流末回传的 assistant 消息 id(用于按需出图后把 chart_config 回写落库)
    onMessageId?: (id: number) => void
  },
) => Promise<void>
