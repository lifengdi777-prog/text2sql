import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from core.lifespan import lifespan
from core.log import logger
from fastapi.middleware.cors import CORSMiddleware
from api.agent_router import router as agent_router
from api.upload_router import router as upload_router
from api.dataset_query_router import router as dataset_query_router
from api.auth_router import router as auth_router
from api.conversation_router import router as conversation_router
from api.health_router import router as health_router
from api.datasource_router import router as datasource_router
import uvicorn

#FastAPI() 创建整个应用实例
app = FastAPI(lifespan=lifespan)

#添加跨域中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],# 允许所有域名访问
    allow_credentials=True,# 允许携带 Cookie
    allow_methods=["*"],# 允许所有 HTTP 方法（GET/POST/PUT...）
    allow_headers=["*"],# 允许所有请求头
)


# 为每个请求注入 request_id:贯穿日志 + 异常响应,便于排查与对账。
# 客户端可传 X-Request-ID 复用,否则服务端生成;响应头回带同一个 id。
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# 全局兜底异常处理:任何未被业务捕获的异常 → 统一结构化 500(不漏堆栈给前端)+ 记日志。
# 注意:FastAPI 的 HTTPException(401/404 等)有自己的处理器,不会走到这里,语义不变。
# 另:SSE 流"中途"抛的异常发生在响应已开始之后,不归这里管 —— 由 stream_with_history 内的兜底处理。
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "-")
    logger.exception(f"[{rid}] 未处理异常 {request.method} {request.url.path}:{exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误,请稍后重试", "request_id": rid},
    )

#登录鉴权接口(POST /auth/register / POST /auth/login / GET /auth/me)
app.include_router(auth_router)
#把 agent_router 里定义的接口（如 POST /agent/query）挂载到应用上
app.include_router(agent_router)
# 数据集上传相关接口(POST /dataset/upload / GET /dataset / GET /dataset/{id} / DELETE /dataset/{id})
app.include_router(upload_router)
# 数据集查询接口(POST /dataset/{id}/query) —— Excel 数据分析专用,跟主 DW 路径独立
app.include_router(dataset_query_router)
# 会话历史接口(GET/PATCH/DELETE /conversations) —— 主图 + 数据集问答历史持久化
app.include_router(conversation_router)
# 健康检查(GET /healthz 探活 / GET /readyz 依赖就绪) —— 给 LB / K8s 探针与上线自检
app.include_router(health_router)
# 数据源注册接口(POST/GET /datasources, DELETE /datasources/{id}) —— 多数据源接入
app.include_router(datasource_router)

def main():
    print("Hello from wenshu!")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)