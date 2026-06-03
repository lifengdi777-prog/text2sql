from contextlib import asynccontextmanager
from fastapi import FastAPI
from clients.qdrant import qdrant_client
from clients.es import es_client
from clients.mysql import dw_mysql_client, meta_mysql_client, client_registry
from conf.app_config import app_config
from core.log import logger


@asynccontextmanager
async def lifespan(_: FastAPI):
# ↑ yield 之前：应用启动时执行
    # 配置了对象存储就确保 bucket 存在(上传功能用;没配则跳过,不影响原 DW 路径)
    if app_config.s3 is not None:
        try:
            from services.object_store import ensure_bucket
            ensure_bucket()
        except Exception as exc:
            logger.warning(f"对象存储 bucket 初始化失败(上传功能不可用):{exc}")
    yield
# ↓ yield 之后：应用关闭时执行
    # FastAPI 应用结束前执行
    await qdrant_client.close()
    await es_client.close()
    await client_registry.close_all()
    await dw_mysql_client.close()
    await meta_mysql_client.close()