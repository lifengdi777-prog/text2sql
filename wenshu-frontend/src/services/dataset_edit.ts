import axios from 'axios'

import { getToken, redirectToLogin } from '@/lib/authToken'
import { parseSseChunk } from '@/lib/sse'
import type {
  EditSessionResp,
  EditUndoResp,
  EditSummary,
  EditDiff,
  EditSheetPreview,
  EditTurn,
} from '@/types/datasetEdit'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

// REST(JSON / blob)实例
const editApi = axios.create({ baseURL: BASE })
// SSE 实例(text 流式增量读)
const editSse = axios.create({
  baseURL: BASE,
  headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
  responseType: 'text',
})

for (const inst of [editApi, editSse]) {
  inst.interceptors.request.use((config) => {
    const token = getToken()
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })
  inst.interceptors.response.use(
    (res) => res,
    (error) => {
      if (axios.isAxiosError(error) && error.response?.status === 401) redirectToLogin()
      return Promise.reject(error)
    },
  )
}

// ───────────────────────── REST ─────────────────────────
export async function openEditSession(datasetId: number): Promise<EditSessionResp> {
  const { data } = await editApi.post<EditSessionResp>(`/dataset/${datasetId}/edit/session`)
  return data
}

export async function undoEdit(datasetId: number, sessionId: number): Promise<EditUndoResp> {
  const { data } = await editApi.post<EditUndoResp>(`/dataset/${datasetId}/edit/${sessionId}/undo`)
  return data
}

/** 翻页:取某 sheet 的某一页(0-based)。 */
export async function previewPage(
  datasetId: number,
  sessionId: number,
  sheet: string,
  page: number,
  size = 20,
): Promise<EditSheetPreview> {
  const { data } = await editApi.get<EditSheetPreview>(
    `/dataset/${datasetId}/edit/${sessionId}/preview`,
    { params: { sheet, page, size } },
  )
  return data
}

export async function discardEditSession(datasetId: number, sessionId: number): Promise<void> {
  await editApi.delete(`/dataset/${datasetId}/edit/${sessionId}`)
}

/** 下载保样式 xlsx:取 blob → 触发浏览器下载。 */
export async function downloadEdit(datasetId: number, sessionId: number): Promise<void> {
  const res = await editApi.get(`/dataset/${datasetId}/edit/${sessionId}/download`, {
    responseType: 'blob',
  })
  // 从 content-disposition 取文件名(filename*=UTF-8''...),取不到给个默认名
  const cd = (res.headers['content-disposition'] as string | undefined) ?? ''
  const fn = /filename\*=UTF-8''([^;]+)/i.exec(cd)?.[1]
  const filename = fn ? decodeURIComponent(fn) : `编辑结果_${datasetId}.xlsx`
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// ───────────────────────── SSE:一轮编辑 ─────────────────────────
interface StreamOpts {
  onStep: (turn: EditTurn) => void
  signal?: AbortSignal
}

function applyEvent(turn: EditTurn, ev: Record<string, unknown>): EditTurn {
  const step = ev.step as string
  const status = ev.status as 'running' | 'success' | 'error'
  const data = (ev.data ?? null) as Record<string, unknown> | null
  const finish = ev.finish === true

  // 维护步骤列表
  const steps = [...turn.steps]
  const i = steps.findIndex((s) => s.step === step)
  if (i >= 0) steps[i] = { step, status }
  else steps.push({ step, status })
  const next: EditTurn = { ...turn, steps }

  if (step === '生成变更' && data) {
    next.sql = (data.sql as string) ?? null
    next.reason = (data.reason as string) ?? null
  }
  if (data && typeof data.guidance === 'string') {
    next.guidance = data.guidance as string
    next.status = 'success'
    return next
  }
  if (step === '待确认' && data) {
    next.status = 'need_confirm'
    next.pendingSql = (data.sql as string) ?? null
    next.summary = (data.summary as EditSummary) ?? null
    next.hint = (data.hint as string) ?? null
    return next
  }
  if (finish && status === 'success' && step === '应用变更' && data) {
    next.status = 'success'
    next.summary = (data.summary as EditSummary) ?? null
    next.diff = (data.diff as EditDiff) ?? null
    next.preview = (data.preview as EditSheetPreview) ?? null
    if (ev.sql) next.sql = ev.sql as string
  }
  if (status === 'error') {
    next.status = 'error'
    next.error =
      (data?.error as string) ??
      (Array.isArray(data?.issues) ? (data!.issues as string[]).join('; ') : '处理失败')
  }
  return next
}

export async function streamEditMessage(
  datasetId: number,
  sessionId: number,
  instruction: string,
  confirmed: boolean,
  activeSheet: string | null,
  opts: StreamOpts,
): Promise<EditTurn> {
  let turn: EditTurn = {
    id: crypto.randomUUID(),
    instruction,
    steps: [],
    sql: null,
    reason: null,
    status: 'streaming',
    summary: null,
    diff: null,
    preview: null,
    pendingSql: null,
    hint: null,
    error: null,
    guidance: null,
  }
  opts.onStep(turn)

  let processed = 0
  let rest = ''
  await editSse.post(
    `/dataset/${datasetId}/edit/${sessionId}/message`,
    { instruction, confirmed, active_sheet: activeSheet },
    {
      signal: opts.signal,
      onDownloadProgress: (pe) => {
        const xhr = pe.event?.target as XMLHttpRequest | undefined
        const text = xhr?.responseText
        if (typeof text !== 'string' || text.length <= processed) return
        const chunk = text.slice(processed)
        processed = text.length
        const parsed = parseSseChunk(rest + chunk)
        rest = parsed.rest
        for (const ev of parsed.events) {
          // 首个 {session_id} 事件:无 step,跳过
          if (!(ev as { step?: unknown }).step) continue
          turn = applyEvent(turn, ev as unknown as Record<string, unknown>)
          opts.onStep(turn)
        }
      },
    },
  )
  // 流尾残段兜底
  if (rest.trim()) {
    for (const ev of parseSseChunk(`${rest}\n\n`).events) {
      if (!(ev as { step?: unknown }).step) continue
      turn = applyEvent(turn, ev as unknown as Record<string, unknown>)
      opts.onStep(turn)
    }
  }
  return turn
}
