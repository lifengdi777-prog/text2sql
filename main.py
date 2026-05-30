from fastapi import FastAPI
from core.lifespan import lifespan
from fastapi.middleware.cors import CORSMiddleware
from api.agent_router import router as agent_router
from api.upload_router import router as upload_router
from api.dataset_query_router import router as dataset_query_router
from api.auth_router import router as auth_router
from api.conversation_router import router as conversation_router
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

def main():
    print("Hello from wenshu!")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)