import axios from 'axios'

import type { AgentReplyMessage, ChartConfig, ResultRow } from '@/types/agent'
import { getToken, redirectToLogin } from '@/lib/authToken'
import { parseSseChunk, type AgentEvent, type AgentEventData, type AgentResultValue } from '@/lib/sse'

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

  return {
    ...current,
    steps: nextSteps,
    result: isResultEvent ? (event.data as ResultRow[]) : current.result,
    chartConfig: isChartEvent ? (event.data as ChartConfig) : current.chartConfig,
    interpretation: isInterpretationEvent ? (event.data as string) : current.interpretation,
    guideQueries: event.finish && event.guide_queries && event.guide_queries.length > 0
      ? event.guide_queries
      : current.guideQueries,
    // 只有 chart_agent 的 finish 事件才标记流真正结束(它是子图最后一步)
    // 如果只收到 result 事件没收到 chart 事件,仍保持 streaming(防止 UI 闪烁"已完成"后又有进度)
    status: isChartEvent ? 'success' : current.status,
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
    id: crypto.randomUUID(),
    role: 'assistant',
    steps: [],
    result: [],
    chartConfig: null,
    interpretation: null,
    guideQueries: [],
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
  await runStream('/agent/query', { query }, options)
}

// 上传数据集(Excel)问答。身份走 Authorization: Bearer 头,不再随 body 传 user_id。
export async function streamDatasetQuery(
  datasetId: number,
  query: string,
  options: QueryOptions,
): Promise<void> {
  await runStream(`/dataset/${datasetId}/query`, { query }, options)
}

export { toErrorMessage }
