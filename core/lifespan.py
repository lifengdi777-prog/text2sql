from contextlib import asynccontextmanager
from fastapi import FastAPI
from clients.qdrant import qdrant_client
from clients.es import es_client
from clients.mysql import dw_mysql_client, meta_mysql_client


@asynccontextmanager
async def lifespan(_: FastAPI):
# ↑ yield 之前：应用启动时执行（目前为空，什么都不做）    
    yield
# ↓ yield 之后：应用关闭时执行
    # FastAPI 应用结束前执行
    await qdrant_client.close()
    await es_client.close()
    await dw_mysql_client.close()
    await meta_mysql_client.close()