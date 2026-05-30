from pydantic import BaseModel

#这是一个 FastAPI 的请求体模型，用 Pydantic 定义前端传进来的数据结构。
class QueryInput(BaseModel):
    query: str
    # 可选:续聊到已有会话;不传则后端新建会话并通过首个 SSE 事件回传 conversation_id
    conversation_id: int | None = None