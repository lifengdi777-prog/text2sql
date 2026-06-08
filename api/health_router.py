"""健康检查接口。

- GET /healthz:存活探针(liveness)。进程活着就返回 200,**不查任何外部依赖**,要快。
                给 K8s livenessProbe / 负载均衡探活用。
- GET /readyz :就绪探针(readiness)。逐个探 MySQL(meta)/ES/Qdrant 连通性,
                全通才 200,任一不可达返回 503 + 明细。给 K8s readinessProbe / 上线前自检用
                ——依赖没就绪就别往这个实例上接流量。

注:不探 LLM / embedding(外部计费 API,每次探活都打既慢又花钱);它们的故障由请求级
   重试 + 降级兜底处理,不纳入就绪探针。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from clients.es import es_client
from clients.mysql import meta_mysql_client
from clients.qdrant import qdrant_client
from core.log import logger

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    """存活探针:不查依赖,进程在就 ok。"""
    return {"status": "ok"}


async def _check_mysql(client, name: str) -> tuple[str, bool, str]:
    try:
        async with client.session() as s:
            await s.execute(text("SELECT 1"))
        return name, True, ""
    except Exception as exc:
        return name, False, str(exc)


async def _check_es() -> tuple[str, bool, str]:
    try:
        ok = await es_client.client.ping()
        return "elasticsearch", bool(ok), "" if ok else "ping 返回 False"
    except Exception as exc:
        return "elasticsearch", False, str(exc)


async def _check_qdrant() -> tuple[str, bool, str]:
    try:
        await qdrant_client.client.get_collections()
        return "qdrant", True, ""
    except Exception as exc:
        return "qdrant", False, str(exc)


@router.get("/readyz")
async def readyz():
    """就绪探针:并行探所有基础依赖,全通才就绪。"""
    results = await asyncio.gather(
        _check_mysql(meta_mysql_client, "mysql_meta"),
        _check_es(),
        _check_qdrant(),
    )
    deps = {
        name: ({"ok": True} if ok else {"ok": False, "error": err})
        for name, ok, err in results
    }
    all_ok = all(d["ok"] for d in deps.values())
    if not all_ok:
        logger.warning(f"/readyz 依赖未就绪:{deps}")
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "not_ready", "dependencies": deps},
    )
