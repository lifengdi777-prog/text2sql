import axios from 'axios'

import type { DatasetSummary, DatasetDetail, UploadResult } from '@/types/dataset'

// 数据集 REST(非流式),跟 agent.ts 的 SSE 实例分开:这里用普通 JSON。
const datasetApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
})

export async function listDatasets(userId?: string): Promise<DatasetSummary[]> {
  const { data } = await datasetApi.get<DatasetSummary[]>('/dataset', {
    params: userId ? { user_id: userId } : undefined,
  })
  return data
}

export async function getDataset(datasetId: number): Promise<DatasetDetail> {
  const { data } = await datasetApi.get<DatasetDetail>(`/dataset/${datasetId}`)
  return data
}

export async function uploadDataset(
  file: File,
  userId = 'anonymous',
  onProgress?: (percent: number) => void,
): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', userId)

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
