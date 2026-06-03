import axios from 'axios'

import { getToken, redirectToLogin } from '@/lib/authToken'
import type {
  DatasourceSummary,
  DatasourceTable,
  DatasourceRegisterPayload,
  DatasourceMeta,
  MetaTable,
  MetaMetric,
} from '@/types/datasource'

// MySQL 数据源 REST(非流式)。身份走 JWT(Authorization: Bearer)。
const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL ?? '' })

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) redirectToLogin()
    return Promise.reject(error)
  },
)

function toError(err: unknown, fallback: string): Error {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: string } | undefined)?.detail
    return new Error(detail || err.message || fallback)
  }
  return err instanceof Error ? err : new Error(fallback)
}

export async function listDatasources(): Promise<DatasourceSummary[]> {
  const { data } = await api.get<DatasourceSummary[]>('/datasources')
  return data
}

// 注册:后端先测连通(SELECT 1),通过才入库;失败抛出后端给的原因。
export async function registerDatasource(
  payload: DatasourceRegisterPayload,
): Promise<{ id: string; name: string }> {
  try {
    const { data } = await api.post<{ id: string; name: string }>('/datasources', payload)
    return data
  } catch (err) {
    throw toError(err, '注册数据源失败')
  }
}

export async function deleteDatasource(id: string): Promise<void> {
  await api.delete(`/datasources/${id}`)
}

// 列出该数据源默认库里的表(向导第③步勾选用)
export async function listDatasourceTables(id: string): Promise<DatasourceTable[]> {
  try {
    const { data } = await api.get<DatasourceTable[]>(`/datasources/${id}/tables`)
    return data
  } catch (err) {
    throw toError(err, '获取数据表失败')
  }
}

// 触发接入(草稿+物化)。异步:后端立即返回 building,前端轮询列表看状态。
export async function buildDatasource(id: string, tables: string[]): Promise<void> {
  try {
    await api.post(`/datasources/${id}/build`, { tables })
  } catch (err) {
    throw toError(err, '触发构建失败')
  }
}

// 读取数据源元数据(供编辑页加载)
export async function getDatasourceMeta(id: string): Promise<DatasourceMeta> {
  try {
    const { data } = await api.get<DatasourceMeta>(`/datasources/${id}/meta`)
    return data
  } catch (err) {
    throw toError(err, '获取元数据失败')
  }
}

// 保存编辑后的元数据(后端异步重物化:重嵌 Qdrant / 重灌 ES)。
// 需双重确认:登录账号密码 + 该数据源的数据库密码,后端校验通过才保存。
export async function saveDatasourceMeta(
  id: string,
  config: { tables: MetaTable[]; metrics: MetaMetric[] },
  userPassword: string,
  dbPassword: string,
): Promise<void> {
  try {
    await api.put(`/datasources/${id}/meta`, {
      config,
      user_password: userPassword,
      db_password: dbPassword,
    })
  } catch (err) {
    throw toError(err, '保存元数据失败')
  }
}
