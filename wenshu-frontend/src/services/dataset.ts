import axios from 'axios'

import type { DatasetSummary, DatasetDetail, UploadResult } from '@/types/dataset'
import { getClientId } from '@/lib/clientId'

// 数据集 REST(非流式),跟 agent.ts 的 SSE 实例分开:这里用普通 JSON。
// X-Client-Id 头 = 过渡期匿名身份,后端据此隔离 + 校验数据集归属。
const datasetApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  headers: {
    'X-Client-Id': getClientId(),
  },
})

// 身份走 X-Client-Id 头,后端只返回当前用户自己的数据集,前端不再传 user_id。
export async function listDatasets(): Promise<DatasetSummary[]> {
  const { data } = await datasetApi.get<DatasetSummary[]>('/dataset')
  return data
}

export async function getDataset(datasetId: number): Promise<DatasetDetail> {
  const { data } = await datasetApi.get<DatasetDetail>(`/dataset/${datasetId}`)
  return data
}

export async function uploadDataset(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)

  const { data } = await datasetApi.post<UploadResult>('/dataset/upload', form, {
    onUploadProgress: (e) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    },
  })
  return data
}

export async function deleteDataset(datasetId: number): Promise<void> {
  await datasetApi.delete(`/dataset/${datasetId}`)
}
