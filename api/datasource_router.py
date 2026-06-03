"""数据源注册接口。

- POST   /datasources        注册一个数据源(先测连通,通过才入库;密码加密存)
- GET    /datasources        列出数据源(不含密码)
- DELETE /datasources/{id}   删除数据源(连同它在 meta 库的元数据行)

注册成功后,可对返回的 id 跑 `generate_draft(datasource_id)` 生成 meta 草稿。
"""
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text

from api.deps import get_current_user
from api.schemas import DatasourceBuildInput, DatasourceRegisterInput
from clients.es import es_client
from clients.mysql import MySQLClient, client_registry, meta_mysql_client
from clients.qdrant import qdrant_client
from conf.app_config import DBConfig
from conf.meta_config import MetaConfig
from core.log import logger
from dtos.datasource import DatasourceCreate, DatasourceInfo
from repositories.datasource import DatasourceRepository
from repositories.es import ESRepository
from repositories.mysql import MetaDBRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from scripts.generate_draft import generate_draft
from scripts.materialize import materialize

router = APIRouter(prefix="/datasources")


async def _test_connectivity(cfg: DBConfig) -> None:
    """建一个临时连接跑 SELECT 1;连不上抛 400(带原始错误,方便排查)。"""
    test_client = MySQLClient(cfg)
    try:
        async with test_client.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"数据源连接失败: {exc}")
    finally:
        await test_client.close()


@router.post("")
async def register_datasource(data: DatasourceRegisterInput, user_id: str = Depends(get_current_user)):
    # 1. 先测连通(用填进来的明文密码),连不上直接 400,不留垃圾记录
    cfg = DBConfig(
        host=data.host, port=data.port, user=data.username,
        password=data.password, database=data.default_database or "",
    )
    await _test_connectivity(cfg)

    # 2. 生成 id + 入库(密码由 repo 加密成 password_enc)
    ds_id = "ds_" + uuid4().hex[:12]
    async with meta_mysql_client.session() as session:
        repo = DatasourceRepository(session)
        async with session.begin():
            await repo.add(DatasourceCreate(
                id=ds_id, name=data.name, type=data.type,
                host=data.host, port=data.port, username=data.username,
                password=data.password, default_database=data.default_database,
                created_by=int(user_id),
            ))
    logger.info(f"[/datasources] user_id={user_id} 注册数据源 {ds_id} ({data.name})")
    return {"id": ds_id, "name": data.name}


@router.get("")
async def list_datasources(_: str = Depends(get_current_user)) -> list[DatasourceInfo]:
    async with meta_mysql_client.session() as session:
        repo = DatasourceRepository(session)
        return await repo.list_all()


@router.get("/{datasource_id}/tables")
async def list_datasource_tables(datasource_id: str, _: str = Depends(get_current_user)):
    """列出该数据源默认库里的表(供向导第③步勾选)。只读 information_schema,不灌任何库。"""
    async with meta_mysql_client.session() as session:
        if not await DatasourceRepository(session).exists(datasource_id):
            raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")
    client = await client_registry.get_client(datasource_id)
    async with client.session() as session:
        rows = (await session.execute(text(
            "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
        ))).fetchall()
    return [{"name": r[0], "comment": r[1] or "", "rows": int(r[2]) if r[2] is not None else None} for r in rows]


async def _run_build(datasource_id: str, tables: list[str]) -> None:
    """后台任务:生成草稿 → 物化,成功置 ready,失败置 failed(记 last_error)。"""
    try:
        draft = await generate_draft(datasource_id, tables or None)
        config = MetaConfig.model_validate({"tables": draft["tables"], "metrics": draft["metrics"]})
        stats = await materialize(datasource_id, config)
        async with meta_mysql_client.session() as session:
            async with session.begin():
                await DatasourceRepository(session).set_build_status(
                    datasource_id, "ready", table_count=stats["tables"])
        logger.info(f"[/datasources] {datasource_id} 构建完成: {stats}")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[/datasources] {datasource_id} 构建失败")
        async with meta_mysql_client.session() as session:
            async with session.begin():
                await DatasourceRepository(session).set_build_status(
                    datasource_id, "failed", last_error=str(exc))


@router.post("/{datasource_id}/build")
async def build_datasource(datasource_id: str, data: DatasourceBuildInput,
                           background: BackgroundTasks, user_id: str = Depends(get_current_user)):
    """接入(草稿+物化)。异步执行:立即置 building 并返回,前端轮询 GET /datasources 看状态。"""
    async with meta_mysql_client.session() as session:
        repo = DatasourceRepository(session)
        async with session.begin():
            if not await repo.exists(datasource_id):
                raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")
            await repo.set_build_status(datasource_id, "building")
    background.add_task(_run_build, datasource_id, data.tables)
    logger.info(f"[/datasources] user_id={user_id} 触发构建 {datasource_id}(表数={len(data.tables) or '全部'})")
    return {"status": "building"}


@router.delete("/{datasource_id}")
async def delete_datasource(datasource_id: str, user_id: str = Depends(get_current_user)):
    async with meta_mysql_client.session() as session:
        async with session.begin():
            ds_repo = DatasourceRepository(session)
            deleted = await ds_repo.delete(datasource_id)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")
            # 连带清掉该源在 meta 库的 5 张表行
            await MetaDBRepository(session, datasource_id).clear_all()
    # 再清该源的 Qdrant 向量 + ES 值文档(都是按 datasource_id 过滤删除,不动别的源)
    await ColumnQdrantRepository(qdrant_client.client).delete_by_datasource(datasource_id)
    await MetricQdrantRepository(qdrant_client.client).delete_by_datasource(datasource_id)
    await ESRepository(es_client.client).delete_by_datasource(datasource_id)
    logger.info(f"[/datasources] user_id={user_id} 删除数据源 {datasource_id}(含 meta/Qdrant/ES 清理)")
    return {"deleted": datasource_id}
