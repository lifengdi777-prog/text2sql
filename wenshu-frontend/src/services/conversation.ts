import axios from 'axios'

import { getToken, redirectToLogin } from '@/lib/authToken'
import type { AgentReplyMessage, ChatMessage } from '@/types/agent'

// 会话历史 REST(非流式),复用与 dataset.ts 一致的 Bearer 鉴权拦截器。
const convApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
})

convApi.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

convApi.interceptors.response.use(
  (res) => res,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      redirectToLogin()
    }
    return Promise.reject(error)
  },
)

export type ConversationSource = 'db' | 'dataset'

export interface ConversationBrief {
  id: number
  source: ConversationSource
  dataset_id: number | null
  title: string
  created_at: string | null
  updated_at: string | null
}

interface StoredMessage {
  id: number
  role: 'user' | 'assistant'
  content: string | null
  payload: Record<string, unknown> | null
  created_at: string | null
}

interface ConversationDetail extends ConversationBrief {
  messages: StoredMessage[]
}

// 新建一个空白会话(用户起名);返回新会话
export async function createConversation(
  source: ConversationSource,
  title: string,
  datasetId?: number,
): Promise<ConversationBrief> {
  const { data } = await convApi.post<ConversationBrief>('/conversations', {
    source,
    title,
    dataset_id: source === 'dataset' ? (datasetId ?? null) : null,
  })
  return data
}

// 列表:主图传 source='db';数据集传 source='dataset' + datasetId
export async function listConversations(
  source: ConversationSource,
  datasetId?: number,
): Promise<ConversationBrief[]> {
  const params: Record<string, string | number> = { source }
  if (source === 'dataset' && datasetId != null) params.dataset_id = datasetId
  const { data } = await convApi.get<ConversationBrief[]>('/conversations', { params })
  return data
}

// 详情:把后端存的消息转成前端可直接渲染的 ChatMessage[]
export async function getConversationMessages(conversationId: number): Promise<ChatMessage[]> {
  const { data } = await convApi.get<ConversationDetail>(`/conversations/${conversationId}`)
  return data.messages.map(toChatMessage)
}

export async function renameConversation(conversationId: number, title: string): Promise<void> {
  await convApi.patch(`/conversations/${conversationId}`, { title })
}

export async function deleteConversation(conversationId: number): Promise<void> {
  await convApi.delete(`/conversations/${conversationId}`)
}

// 存储消息 → 前端 ChatMessage。assistant 的 payload 形状即 AgentReplyMessage(缺 id/role),补齐即可原样渲染。
function toChatMessage(m: StoredMessage): ChatMessage {
  if (m.role === 'user') {
    return { id: `hist-${m.id}`, role: 'user', content: m.content ?? '' }
  }
  const p = (m.payload ?? {}) as Partial<AgentReplyMessage>
  return {
    id: `hist-${m.id}`,
    role: 'assistant',
    steps: p.steps ?? [],
    result: p.result ?? [],
    chartConfig: p.chartConfig ?? null,
    interpretation: p.interpretation ?? null,
    sql: p.sql ?? null,
    guideQueries: p.guideQueries ?? [],
    // 扇出风险标记 + 文案:历史回放时重现警告样式(老数据无此字段则按非扇出处理)
    fanout: p.fanout ?? false,
    fanoutMessage: p.fanoutMessage ?? null,
    status: p.status ?? 'success',
  }
}
