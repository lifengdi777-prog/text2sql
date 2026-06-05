import axios from 'axios'

import type {
  DatasetSummary,
  DatasetDetail,
  UploadResult,
  HeaderReview,
  HeaderConfirmSpec,
} from '@/types/dataset'
import { getToken, redirectToLogin } from '@/lib/authToken'

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

/**
 * 上传 Excel(**非阻塞**):后端秒建一行(status=cleaning)立即返回 dataset_id,
 * AI 解析/清洗/索引都在后台跑。前端拿到后靠列表轮询 status 等卡片变 ready/failed。
 * onProgress 只反映字节上传进度。
 */
export async function uploadDataset(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  try {
    const { data } = await datasetApi.post<UploadResult>('/dataset/upload', form, {
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    })
    return data
  } catch (err) {
    if (axios.isAxiosError(err)) {
      const detail = (err.response?.data as { detail?: string } | undefined)?.detail
      throw new Error(detail || err.message || '上传失败')
    }
    throw err
  }
}

export async function deleteDataset(datasetId: number): Promise<void> {
  await datasetApi.delete(`/dataset/${datasetId}`)
}

/** 取「待确认表头」的预览载荷(各 sheet 前若干行网格 + 建议表头),用于渲染确认弹窗。 */
export async function getHeaderReview(datasetId: number): Promise<HeaderReview> {
  const { data } = await datasetApi.get<HeaderReview>(`/dataset/${datasetId}/header-review`)
  return data
}

/** 提交用户手选的表头(每个 sheet 的 data_start_row + columns),后端后台重跑解析入库。 */
export async function confirmHeader(
  datasetId: number,
  sheets: Record<string, HeaderConfirmSpec>,
): Promise<void> {
  await datasetApi.post(`/dataset/${datasetId}/header-confirm`, { sheets })
}
