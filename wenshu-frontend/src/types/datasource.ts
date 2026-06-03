// MySQL 数据源(连接 + 接入状态)。区别于「数据集」(上传的 Excel)。
export interface DatasourceSummary {
  id: string
  name: string
  type: string
  host: string
  port: number
  username: string
  default_database: string | null
  created_by: number | null
  status: string
  // 接入构建状态:pending(刚注册) / building(草稿+物化中) / ready(可问数) / failed
  build_status: string | null
  // 已物化的表数
  table_count: number | null
}

// 向导第③步:列出库里的表供勾选
export interface DatasourceTable {
  name: string
  comment: string
  rows: number | null
}

// 注册入参(密码明文上送,后端加密存)
export interface DatasourceRegisterPayload {
  name: string
  host: string
  port: number
  username: string
  password: string
  type?: string
  default_database?: string | null
}
