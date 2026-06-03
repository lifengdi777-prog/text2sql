from pydantic import BaseModel

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