from pydantic import BaseModel

from conf.meta_config import MetaConfig, TableConfig, MetricConfig

#这是一个 FastAPI 的请求体模型，用 Pydantic 定义前端传进来的数据结构。
class QueryInput(BaseModel):
    query: str
    # 可选:续聊到已有会话;不传则后端新建会话并通过首个 SSE 事件回传 conversation_id
    conversation_id: int | None = None
    # 多数据源:本次问数针对哪个数据源 / 哪个库。不传则用默认数据源(现有手工库)。
    datasource_id: str = "ds_default"
    database: str | None = None


class DatasourceRegisterInput(BaseModel):
    """注册新数据源的入参。id 由后端生成,created_by 取自登录态,均不由前端传。"""
    name: str
    host: str
    port: int
    username: str
    password: str
    type: str = "mysql"
    default_database: str | None = None


class DatasourceBuildInput(BaseModel):
    """构建(草稿+物化)入参。tables 为空则接入该库全部表;否则只接入选中的表。"""
    tables: list[str] = []


# 三个 Tab 各存各的,都要双密码(账号 + 数据库)。
class TablesUpdateInput(BaseModel):
    """保存「表元数据」:只写 table_info/column_info + 重嵌列向量 + 重灌 ES。"""
    tables: list[TableConfig]
    user_password: str
    db_password: str


class MetricsUpdateInput(BaseModel):
    """保存「指标信息」:只写 metric_info/column_metric + 重嵌指标向量。"""
    metrics: list[MetricConfig]
    user_password: str
    db_password: str


class RelationshipEdge(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    description: str | None = None


class RelationshipsUpdateInput(BaseModel):
    """保存「表关系」:只重写 data_relationship,不重建索引。"""
    relationships: list[RelationshipEdge] = []
    user_password: str
    db_password: str


class DatasourceDeleteInput(BaseModel):
    """删除数据源:需账号密码 + 该源数据库密码双重确认(破坏性操作)。"""
    user_password: str
    db_password: str