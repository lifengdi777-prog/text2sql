import axios from 'axios'

import type { DatasetSummary, DatasetDetail, UploadResult } from '@/types/dataset'
import { getToken, redirectToLogin } from '@/lib/authToken'
import { parseSseChunk, type AgentEvent } from '@/lib/sse'

// 数据集 REST(非流式),跟 agent.ts 的 SSE 实例分开:这里用普通 JSON。
// 身份走 JWT(Authorization: Bearer),后端据此隔离 + 校验数据集归属。
const datasetApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
})

datasetApi.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

datasetApi.interceptors.response.use(
  (res) => res,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      redirectToLogin()
    }
    return Promise.reject(error)
  },
)

// 身份走 Bearer 头,后端只返回当前用户自己的数据集,前端不再传 user_id。
export async function listDatasets(): Promise<DatasetSummary[]> {
  const { data } = await datasetApi.get<DatasetSummary[]>('/dataset')
  return data
}

export async function getDataset(datasetId: number): Promise<DatasetDetail> {
  const { data } = await datasetApi.get<DatasetDetail>(`/dataset/${datasetId}`)
  return data
}

export interface UploadStreamHandlers {
  onProgress?: (percent: number) => void           // 字节上传进度 0~100
  onStep?: (step: string, status: string) => void  // 服务端处理阶段(SSE 实时)
}

/**
 * 流式上传:服务端用 SSE 实时推送各处理阶段(AI 识别表头 / 清洗字段 / 写入存储),
 * 最后一条 finish=true 携带数据集摘要。复用查询那套"axios 读增长 responseText + parseSseChunk"。
 */
export async function uploadDataset(
  file: File,
  handlers: UploadStreamHandlers = {},
): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)

  let processed = 0
  let rest = ''
  let result: UploadResult | null = null
  let streamError: string | null = null

  const handleEvent = (ev: AgentEvent) => {
    if (ev.finish) {
      const data = (ev.data ?? null) as Record<string, unknown> | null
      if (ev.status === 'error') {
        streamError = (data?.error as string) || '入库失败'
      } else {
        result = data as unknown as UploadResult
      }
    } else {
      handlers.onStep?.(ev.step, ev.status)
    }
  }

  try {
    await datasetApi.post('/dataset/upload', form, {
      responseType: 'text',
      headers: { Accept: 'text/event-stream' },
      onUploadProgress: (e) => {
        if (handlers.onProgress && e.total) {
          handlers.onProgress(Math.round((e.loaded / e.total) * 100))
        }
      },
      onDownloadProgress: (pe) => {
        const xhr = pe.event?.target as XMLHttpRequest | undefined
        const text = xhr?.responseText
        if (typeof text !== 'string' || text.length <= processed) return
        const chunk = text.slice(processed)
        processed = text.length
        const parsed = parseSseChunk(rest + chunk)
        rest = parsed.rest
        parsed.events.forEach(handleEvent)
      },
    })
  } catch (err) {
    // 校验类错误:后端在流开始前直接 4xx(JSON detail)
    if (axios.isAxiosError(err)) {
      const detail = (err.response?.data as { detail?: string } | undefined)?.detail
      throw new Error(detail || err.message || '上传失败')
    }
    throw err
  }

  // flush:最后一段可能没收到 \n\n,补一个再解析
  if (rest.trim()) {
    parseSseChunk(`${rest}\n\n`).events.forEach(handleEvent)
  }

  if (streamError) throw new Error(streamError)
  if (!result) throw new Error('上传未返回结果')
  return result
}

export async function deleteDataset(datasetId: number): Promise<void> {
  await datasetApi.delete(`/dataset/${datasetId}`)
}
