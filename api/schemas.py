from pydantic import BaseModel

#这是一个 FastAPI 的请求体模型，用 Pydantic 定义前端传进来的数据结构。
#这个接口只接受一个字符串字段 query，其他的一律拒绝"
class QueryInput(BaseModel):
    query: str