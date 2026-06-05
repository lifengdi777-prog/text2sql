// 「智能助手」编辑相关类型,对齐后端 api/dataset_edit_router.py + runner.py 的返回结构。

export type EditCellValue = string | number | boolean | null

// 单 sheet 的当前预览(后端 EditWorkbook.preview + sheet 名)
export interface EditSheetPreview {
  sheet: string
  columns: string[]
  rows: Record<string, EditCellValue>[]
  total: number
}

// 已应用的一条操作(用于重建历史气泡)
export interface EditOpRecord {
  seq: number
  nl: string | null
  sql: string | null
  op_type: string
  target_sheet: string | null
  affected: EditSummary | null
}

// POST /edit/session 与 /undo 的返回
export interface EditSessionResp {
  session_id: number
  ops_count: number
  sheets: EditSheetPreview[]
  ops: EditOpRecord[]
}

export interface EditUndoResp {
  undone: boolean
  ops_count: number
  sheets: EditSheetPreview[]
  ops: EditOpRecord[]
}

export interface EditCellChange {
  // 直播流里带 row_id/excel_row;历史还原(来自 op.affected.changes)只有 col/old/new
  row_id?: string
  excel_row?: number | null
  col: string
  old: EditCellValue
  new: EditCellValue
}

// 变更摘要(runner._summary + 落库时附带的明细 changes)
export interface EditSummary {
  changed: number
  deleted: number
  new_rows: number
  added_cols: string[]
  dropped_cols: string[]
  renames: string[] // ["旧→新", ...]
  // 落库的明细(列:旧→新,截前若干条),供刷新后历史气泡还原
  changes?: EditCellChange[]
}

// 应用变更 finish 事件里的 diff(已截断)
export interface EditDiff {
  cell_changes: EditCellChange[]
  deleted: { row_id: string; excel_row: number | null }[]
  renames: { old: string; new: string }[]
  added_cols: string[]
  dropped_cols: string[]
}

// 一轮对话(右侧问答区的一条助手回应,边流边累积)
export interface EditTurn {
  id: string
  instruction: string
  steps: { step: string; status: 'running' | 'success' | 'error' }[]
  sql: string | null
  reason: string | null
  // streaming 流中;success 已应用;need_confirm 待确认;error 失败
  status: 'streaming' | 'success' | 'need_confirm' | 'error'
  summary: EditSummary | null
  diff: EditDiff | null
  preview: EditSheetPreview | null // 受影响 sheet 的最新预览
  pendingSql: string | null // need_confirm 时待执行的 SQL
  hint: string | null
  error: string | null
}
