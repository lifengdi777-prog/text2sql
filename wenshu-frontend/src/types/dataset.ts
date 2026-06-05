// 上传数据集(Excel)相关类型,对齐后端 api/upload_router.py 的返回结构。

// cleaning 清洗入库中 / needs_header 表头可疑待用户确认 / indexing ES 值索引建设中 / ready 全就绪 / failed / deleting
export type DatasetStatus =
  | 'cleaning'
  | 'needs_header'
  | 'indexing'
  | 'ready'
  | 'failed'
  | 'deleting'

// GET /dataset 列表项
export interface DatasetSummary {
  dataset_id: number
  user_id: string
  name: string
  original_filename: string | null
  status: DatasetStatus
  sheet_count: number
  total_rows: number
  created_at: string | null
  // 后台处理失败时的原因(status=failed 时有值),卡片用来提示
  error_message?: string | null
}

// schema_json 里单列的 profile(excel_ingest.profile_columns 产出)
export interface ColumnProfile {
  name: string
  dtype: string
  semantic_type: 'numeric' | 'temporal' | 'categorical'
  cardinality: number
  null_count: number
  // 小基数列:全枚举
  values?: Array<string | number | boolean | null>
  is_high_cardinality?: boolean
  // 大基数/数值列:top-K 频繁值
  top_k?: Array<string | number | boolean | null>
  // 数值/时间列范围
  min?: string | number
  max?: string | number
  mean?: number
}

export interface SheetSchema {
  row_count: number
  parquet_file: string
  columns: ColumnProfile[]
}

export interface DatasetSchema {
  sheets: Record<string, SheetSchema>
}

// GET /dataset/{id} 详情
export interface DatasetDetail extends DatasetSummary {
  folder_path: string | null
  schema: DatasetSchema | null
}

// ── 表头确认(needs_header)相关,对齐 api/upload_router.py 的 header-review / header-confirm ──

// 单个 sheet 的待确认信息:前若干行原始网格 + 建议表头 + 是否可疑
export interface HeaderSheetReview {
  grid: string[][]
  width: number
  suggested: { data_start_row: number; columns: string[]; header_rows: number[] }
  flagged: boolean
}

// GET /dataset/{id}/header-review
export interface HeaderReview {
  dataset_id: number
  status: DatasetStatus
  needs_review: boolean
  filename?: string | null
  sheets?: Record<string, HeaderSheetReview>
}

// POST /dataset/{id}/header-confirm 的单 sheet 提交规格
export interface HeaderConfirmSpec {
  data_start_row: number
  columns: string[]
}

// POST /dataset/upload 返回(非阻塞:只确认已建行,后续状态靠列表轮询)
export interface UploadResult {
  ok: boolean
  dataset_id: number
  name: string
  status?: DatasetStatus
  sheet_count: number
  total_rows: number
  duplicated: boolean
  folder_path?: string
}
