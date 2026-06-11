import axios from 'axios'

import type { AgentReplyMessage, ChartConfig, ResultRow } from '@/types/agent'
import { getToken, redirectToLogin } from '@/lib/authToken'
import { parseSseChunk, type AgentEvent, type AgentEventData, type AgentResultValue } from '@/lib/sse'
import { uuid } from '@/lib/uuid'

const agentApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  headers: {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  },
  responseType: 'text',
})

// 每个请求带上 JWT;token 失效(401)统一跳登录页。
agentApi.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

agentApi.interceptors.response.use(
  (res) => res,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      redirectToLogin()
    }
    return Promise.reject(error)
  },
)

interface QueryOptions {
  signal?: AbortSignal
  onStep: (message: AgentReplyMessage) => void
  // 续聊到已有会话;不传则后端新建
  conversationId?: number | null
  // 后端回传(新建或确认)的 conversation_id,通过首个 SSE 事件送达
  onConversation?: (id: number) => void
  // 后端回传的 assistant 消息 id,通过末个 SSE 事件送达(按需出图后回写落库用)
  onMessageId?: (id: number) => void
  // 针对哪个数据源问数(必须显式给;不再有隐式默认源)
  datasourceId?: string
}

function toErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    return error.message || '查询请求失败，请稍后重试。'
  }

  if (error instanceof Error) {
    return error.message
  }

  return '查询请求失败，请稍后重试。'
}

function isRawRowsData(data: AgentEventData): data is Record<string, AgentResultValue>[] {
  // execute_sql 的成功事件:data 是 raw rows 数组
  return Array.isArray(data)
}

function isFanoutData(data: AgentEventData): data is { fanout: boolean; message?: string } {
  // fanout_clarify 的事件:data 是带 fanout:true 的对象(扇出风险引导)
  return (
    !!data &&
    !Array.isArray(data) &&
    typeof data === 'object' &&
    (data as Record<string, unknown>).fanout === true
  )
}

function isChartConfigData(data: AgentEventData): data is ChartConfig {
  // chart_agent 的 finish 事件:data 是带 chart_type 字段的对象
  return (
    !!data &&
    !Array.isArray(data) &&
    typeof data === 'object' &&
    'chart_type' in data &&
    typeof (data as Record<string, unknown>).chart_type === 'string'
  )
}

function mergeReplyMessage(
  current: AgentReplyMessage,
  event: AgentEvent,
): AgentReplyMessage {
  const stepIndex = current.steps.findIndex((item) => item.step === event.step)
  const nextSteps = [...current.steps]

  if (stepIndex >= 0) {
    const existingStep = nextSteps[stepIndex]
    if (existingStep) {
      nextSteps[stepIndex] = { ...existingStep, status: event.status }
    }
  } else {
    nextSteps.push({ step: event.step, status: event.status })
  }

  // 一次成功的请求会有两次 finish=true 事件:
  //   1) execute_sql:data=raw rows 数组 → 更新 result(老协议)
  //   2) chart_agent:data=chart_config 对象 → 更新 chartConfig(新协议)
  // 失败请求只有 chart_agent 的 finish=true(error 卡)
  const isResultEvent = event.finish && isRawRowsData(event.data)
  const isChartEvent = event.finish && isChartConfigData(event.data)
  // interpret_result 节点的事件:step="数据解读",data 是纯文本(不带 finish)
  const isInterpretationEvent = event.step === '数据解读' && typeof event.data === 'string'

  const nextChartConfig = isChartEvent ? (event.data as ChartConfig) : current.chartConfig

  // 「生成图表」与「数据解读」是并行分支,执行链路要等两者都完成才折叠:
  //  - 图表完成:收到 chart_agent 的 finish 事件(chartConfig 落定)
  //  - 解读完成:数据解读步骤进入终态(success/error)
  // 数据解读是流式的(running 阶段就带累计文本),所以必须按步骤终态判断,不能只看 interpretation 是否非空。
  // 错误/空结果场景不产生数据解读事件,这里不会置 success,改由 ChatConsole 流结束后的兜底逻辑置 success。
  // 图表改为前端按需点击生成,不再随流自动产出 → 收尾只看「数据解读」是否完成
  // (错误/空结果场景不产生解读事件,由 ChatConsole 流结束兜底置 success)
  const interpretSettled = nextSteps.some(
    (s) => s.step === '数据解读' && (s.status === 'success' || s.status === 'error'),
  )

  return {
    ...current,
    steps: nextSteps,
    result: isResultEvent ? (event.data as ResultRow[]) : current.result,
    chartConfig: nextChartConfig,
    interpretation: isInterpretationEvent ? (event.data as string) : current.interpretation,
    // 执行成功事件带上的真正执行 SQL;后续事件没有 sql 时保留已存的
    sql: event.sql ?? current.sql,
    guideQueries: event.finish && event.guide_queries && event.guide_queries.length > 0
      ? event.guide_queries
      : current.guideQueries,
    // 扇出风险:命中时记下标记 + 说明文案,供 ChatConsole 用危险色 + 警告图标渲染引导区
    fanout: isFanoutData(event.data) ? true : current.fanout,
    fanoutMessage: isFanoutData(event.data)
      ? (event.data.message ?? null)
      : current.fanoutMessage,
    status: interpretSettled ? 'success' : current.status,
  }
}

function isResultValue(value: unknown): value is AgentResultValue {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value)
}

function normalizeEvent(event: AgentEvent): AgentEvent | null {
  if (!event.step || !event.status) {
    return null
  }

  const guideQueries = Array.isArray(event.guide_queries)
    ? event.guide_queries.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : null

  // 非数组 data(对象或 null)直接放行,chart_config 由 mergeReplyMessage 识别 chart_type 字段
  if (!Array.isArray(event.data)) {
    return {
      ...event,
      data: event.data ?? null,
      guide_queries: guideQueries,
    }
  }

  // 数组 data(raw rows):规范化每个值
  const normalizedRows = event.data.map((row) => {
    const normalizedRow: ResultRow = {}

    for (const [key, value] of Object.entries(row)) {
      normalizedRow[key] = isResultValue(value) ? value : String(value)
    }

    return normalizedRow
  })

  return {
    ...event,
    data: normalizedRows,
    guide_queries: guideQueries,
  }
}

// DW 问答和数据集问答的 SSE 协议完全一致,只是 URL/body 不同,共用这个 streamer。
async function runStream(
  url: string,
  body: Record<string, unknown>,
  options: QueryOptions,
): Promise<void> {
  let processedLength = 0
  let rest = ''
  let message: AgentReplyMessage = {
    id: uuid(),
    role: 'assistant',
    steps: [],
    result: [],
    chartConfig: null,
    interpretation: null,
    sql: null,
    guideQueries: [],
    fanout: false,
    fanoutMessage: null,
    status: 'streaming',
  }

  await agentApi.post(url, body, {
    signal: options.signal,
    onDownloadProgress: (progressEvent) => {
      const target = progressEvent.event?.target as XMLHttpRequest | undefined
      const responseText = target?.responseText

      if (typeof responseText !== 'string' || responseText.length <= processedLength) {
        return
      }

      const chunk = responseText.slice(processedLength)
      processedLength = responseText.length
      const parsed = parseSseChunk(rest + chunk)
      rest = parsed.rest

      for (const event of parsed.events) {
        // 首个事件 {conversation_id: N}:回传给上层,不当作步骤渲染
        const convId = (event as { conversation_id?: unknown }).conversation_id
        if (typeof convId === 'number') {
          options.onConversation?.(convId)
          continue
        }
        // 末个事件 {assistant_message_id: N}:回传给上层(按需出图后回写落库用),不当作步骤渲染
        const msgId = (event as { assistant_message_id?: unknown }).assistant_message_id
        if (typeof msgId === 'number') {
          options.onMessageId?.(msgId)
          continue
        }

        const normalizedEvent = normalizeEvent(event)
        if (!normalizedEvent) {
          continue
        }

        message = mergeReplyMessage(message, normalizedEvent)
        options.onStep(message)
      }
    }
  })

  if (rest.trim()) {
    const parsed = parseSseChunk(`${rest}\n\n`)

    for (const event of parsed.events) {
      const normalizedEvent = normalizeEvent(event)
      if (!normalizedEvent) {
        continue
      }

      message = mergeReplyMessage(message, normalizedEvent)
      options.onStep(message)
    }
  }
}

// DW(MySQL 数仓)问答
export async function streamAgentQuery(query: string, options: QueryOptions): Promise<void> {
  await runStream(
    '/agent/query',
    {
      query,
      conversation_id: options.conversationId ?? null,
      datasource_id: options.datasourceId,
    },
    options,
  )
}

// 上传数据集(Excel)问答。身份走 Authorization: Bearer 头,不再随 body 传 user_id。
export async function streamDatasetQuery(
  datasetId: number,
  query: string,
  options: QueryOptions,
): Promise<void> {
  await runStream(
    `/dataset/${datasetId}/query`,
    { query, conversation_id: options.conversationId ?? null },
    options,
  )
}

// 按需生成图表:把问数结果行 + 问题发给后端,返回 chart_config(用户点「生成图表」时调)。
export async function generateChart(
  rows: ResultRow[],
  query: string,
): Promise<ChartConfig | null> {
  const { data } = await agentApi.post<{ chart_config: ChartConfig | null }>(
    '/chart',
    { rows, query },
    { responseType: 'json' },
  )
  return data?.chart_config ?? null
}

// 按需分析报告:把结果行 + 问题 + SQL 发给后端,返回自包含 HTML(新标签页打开)。
export async function generateReport(
  rows: ResultRow[],
  query: string,
  sql: string | null,
): Promise<string> {
  const { data } = await agentApi.post<string>(
    '/report',
    { rows, query, sql },
    { responseType: 'text', headers: { Accept: 'text/html' } },
  )
  return data
}

// 问数页空状态的「历史热门问题」:本数据源缓存里命中最多的问题。
// 点选后逐字提问 → 必然精确命中 SQL 缓存,秒出结果。拉取失败返回空数组(区块不显示),不影响问数。
export async function fetchHotQuestions(datasourceId: string, limit = 6): Promise<string[]> {
  try {
    const { data } = await agentApi.get<{ questions: string[] }>('/agent/hot-questions', {
      params: { datasource_id: datasourceId, limit },
      responseType: 'json',
    })
    return Array.isArray(data?.questions) ? data.questions : []
  } catch {
    return []
  }
}

export { toErrorMessage }
