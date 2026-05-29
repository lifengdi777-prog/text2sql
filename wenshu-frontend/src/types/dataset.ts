// 上传数据集(Excel)相关类型,对齐后端 api/upload_router.py 的返回结构。

export type DatasetStatus = 'cleaning' | 'ready' | 'failed' | 'deleting'

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

// POST /dataset/upload 返回
export interface UploadResult {
  ok: boolean
  dataset_id: number
  name: string
  folder_path: string
  sheet_count: number
  total_rows: number
  duplicated: boolean
  sheets?: Array<{
    sheet: string
    row_count: number
    columns: string[]
  }>
}
