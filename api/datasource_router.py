"""数据源注册接口。

- POST   /datasources        注册一个数据源(先测连通,通过才入库;密码加密存)
- GET    /datasources        列出数据源(不含密码)
- DELETE /datasources/{id}   删除数据源(连同它在 meta 库的元数据行)

注册成功后,可对返回的 id 跑 `generate_draft(datasource_id)` 生成 meta 草稿。
"""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from api.deps import get_current_user
from api.schemas import DatasourceRegisterInput
from clients.mysql import MySQLClient, meta_mysql_client
from conf.app_config import DBConfig, DEFAULT_DATASOURCE_ID
from core.log import logger
from dtos.datasource import DatasourceCreate, DatasourceInfo
from repositories.datasource import DatasourceRepository
from repositories.mysql import MetaDBRepository

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


@router.delete("/{datasource_id}")
async def delete_datasource(datasource_id: str, user_id: str = Depends(get_current_user)):
    if datasource_id == DEFAULT_DATASOURCE_ID:
        raise HTTPException(status_code=400, detail="默认数据源不可删除")
    async with meta_mysql_client.session() as session:
        async with session.begin():
            ds_repo = DatasourceRepository(session)
            deleted = await ds_repo.delete(datasource_id)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")
            # 连带清掉该源在 meta 库的 5 张表行(Qdrant/ES 的清理待 materialize 配套补)
            await MetaDBRepository(session, datasource_id).clear_all()
    logger.info(f"[/datasources] user_id={user_id} 删除数据源 {datasource_id}")
    return {"deleted": datasource_id}
