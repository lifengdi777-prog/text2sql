from fastapi import FastAPI
from core.lifespan import lifespan
from fastapi.middleware.cors import CORSMiddleware
from api.agent_router import router as agent_router
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

#把 agent_router 里定义的接口（如 POST /agent/query）挂载到应用上
app.include_router(agent_router)

def main():
    print("Hello from wenshu!")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)