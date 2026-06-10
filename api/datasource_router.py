"""数据源注册接口。

- POST   /datasources        注册一个数据源(先测连通,通过才入库;密码加密存)
- GET    /datasources        列出数据源(不含密码)
- DELETE /datasources/{id}   删除数据源(连同它在 meta 库的元数据行)

注册成功后,可对返回的 id 跑 `generate_draft(datasource_id)` 生成 meta 草稿。
"""
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text

from api.deps import get_current_user, require_admin
from api.schemas import (
    DatasourceBuildInput,
    DatasourceDeleteInput,
    DatasourceRegisterInput,
    MetricsUpdateInput,
    RelationshipsUpdateInput,
    TablesUpdateInput,
)
from dtos.meta import DataRelationship
from clients.es import es_client
from clients.mysql import MySQLClient, client_registry, meta_mysql_client
from clients.qdrant import qdrant_client
from conf.app_config import DBConfig
from conf.meta_config import MetaConfig
from core import crypto
from core.log import logger
from dtos.datasource import DatasourceCreate, DatasourceInfo
from repositories.datasource import DatasourceRepository
from repositories.es import ESRepository
from repositories.mysql import MetaDBRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from scripts.generate_draft import generate_draft
from scripts.materialize import materialize, materialize_metrics, materialize_tables
from services.auth import get_user_by_id, verify_password

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
async def register_datasource(data: DatasourceRegisterInput, user_id: str = Depends(require_admin)):
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


@router.get("/{datasource_id}/meta")
async def get_datasource_meta(datasource_id: str, _: str = Depends(get_current_user)):
    """组装该数据源的元数据(5 表)给编辑页:表/列(含只读的 type/examples + 可改的 desc/alias/role/sync)
    + 指标 + 关系(只读)。"""
    async with meta_mysql_client.session() as session:
        ds = await DatasourceRepository(session).get_by_id(datasource_id)
        if ds is None:
            raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")
        join_max_extra, join_k = ds.join_max_extra, ds.join_k   # JOIN 选路参数,回显给编辑页
        meta_repo = MetaDBRepository(session, datasource_id)
        tables = await meta_repo.get_all_tables()
        columns = await meta_repo.get_all_columns()
        metrics = await meta_repo.get_all_metrics()
        relationships = await meta_repo.get_relationships()

    cols_by_table: dict[str, list[dict]] = {}
    for c in columns:
        cols_by_table.setdefault(c.table_id, []).append({
            "name": c.name, "type": c.type, "role": c.role,
            "description": c.description, "alias": c.alias, "sync": c.sync,
            "examples": (c.examples or [])[:5],  # 只读,给点上下文
        })
    return {
        "datasource_id": datasource_id,
        "tables": [
            {"name": t.name, "role": t.role, "description": t.description,
             "columns": cols_by_table.get(t.id, [])}
            for t in tables
        ],
        "metrics": [
            {"name": m.name, "description": m.description,
             "relevant_columns": m.relevant_columns, "alias": m.alias}
            for m in metrics
        ],
        "relationships": [
            {"from_table": r.from_table, "from_column": r.from_column,
             "to_table": r.to_table, "to_column": r.to_column, "description": r.description}
            for r in relationships
        ],
        "join_max_extra": join_max_extra,
        "join_k": join_k,
    }


async def _set_status(datasource_id: str, status: str, **kw) -> None:
    async with meta_mysql_client.session() as session:
        async with session.begin():
            repo = DatasourceRepository(session)
            await repo.set_build_status(datasource_id, status, **kw)
            # 物化/保存元数据成功(置 ready)→ 元数据版本 +1,该源旧 SQL 缓存全部失效。
            if status == "ready":
                await repo.bump_meta_version(datasource_id)


async def _materialize_and_track(datasource_id: str, config: MetaConfig,
                                 rebuild_relationships: bool = True) -> None:
    """物化给定 config(写 meta + 重嵌 Qdrant + 重灌 ES),并更新构建状态。build 和编辑保存共用。
    rebuild_relationships=False(编辑保存)时不重建 data_relationship —— 关系交给人单独管。"""
    try:
        stats = await materialize(datasource_id, config, rebuild_relationships)
        await _set_status(datasource_id, "ready", table_count=stats["tables"])
        logger.info(f"[/datasources] {datasource_id} 物化完成: {stats}")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[/datasources] {datasource_id} 物化失败")
        await _set_status(datasource_id, "failed", last_error=str(exc))


async def _run_build(datasource_id: str, tables: list[str]) -> None:
    """后台任务:生成草稿 → 物化。草稿阶段失败也置 failed。"""
    try:
        draft = await generate_draft(datasource_id, tables or None)
        config = MetaConfig.model_validate({"tables": draft["tables"], "metrics": draft["metrics"]})
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[/datasources] {datasource_id} 草稿生成失败")
        await _set_status(datasource_id, "failed", last_error=str(exc))
        return
    await _materialize_and_track(datasource_id, config)


@router.post("/{datasource_id}/build")
async def build_datasource(datasource_id: str, data: DatasourceBuildInput,
                           background: BackgroundTasks, user_id: str = Depends(require_admin)):
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


async def _verify_passwords(user_id: str, datasource_id: str, user_password: str, db_password: str):
    """三个保存共用的双密码校验:登录账号密码 + 该数据源的数据库密码,任一错即抛。"""
    user = await get_user_by_id(int(user_id))
    if user is None or not verify_password(user_password, user.password_hash):
        raise HTTPException(status_code=403, detail="账号密码错误")
    async with meta_mysql_client.session() as session:
        ds = await DatasourceRepository(session).get_by_id(datasource_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"数据源 {datasource_id} 不存在")
    try:
        db_ok = crypto.decrypt(ds.password_enc) == db_password
    except Exception:  # noqa: BLE001
        db_ok = False
    if not db_ok:
        raise HTTPException(status_code=403, detail="数据库密码错误")


async def _run_save_tables(datasource_id: str, tables) -> None:
    try:
        stats = await materialize_tables(datasource_id, tables)
        await _set_status(datasource_id, "ready", table_count=stats["tables"])
        logger.info(f"[/datasources] {datasource_id} 保存表元数据完成: {stats}")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[/datasources] {datasource_id} 保存表元数据失败")
        await _set_status(datasource_id, "failed", last_error=str(exc))


async def _run_save_metrics(datasource_id: str, metrics) -> None:
    try:
        stats = await materialize_metrics(datasource_id, metrics)
        await _set_status(datasource_id, "ready")
        logger.info(f"[/datasources] {datasource_id} 保存指标完成: {stats}")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[/datasources] {datasource_id} 保存指标失败")
        await _set_status(datasource_id, "failed", last_error=str(exc))


@router.put("/{datasource_id}/tables")
async def update_datasource_tables(datasource_id: str, data: TablesUpdateInput,
                                   background: BackgroundTasks, user_id: str = Depends(require_admin)):
    """保存「表元数据」(异步)。只写 table_info/column_info + 重嵌列向量 + 重灌 ES,不碰指标/关系。"""
    await _verify_passwords(user_id, datasource_id, data.user_password, data.db_password)
    await _set_status(datasource_id, "building")
    background.add_task(_run_save_tables, datasource_id, data.tables)
    logger.info(f"[/datasources] user_id={user_id} 保存表元数据 {datasource_id}({len(data.tables)} 表)")
    return {"status": "building"}


@router.put("/{datasource_id}/metrics")
async def update_datasource_metrics(datasource_id: str, data: MetricsUpdateInput,
                                    background: BackgroundTasks, user_id: str = Depends(require_admin)):
    """保存「指标信息」(异步)。只写 metric_info/column_metric + 重嵌指标向量,不碰表/列/ES/关系。"""
    await _verify_passwords(user_id, datasource_id, data.user_password, data.db_password)
    await _set_status(datasource_id, "building")
    background.add_task(_run_save_metrics, datasource_id, data.metrics)
    logger.info(f"[/datasources] user_id={user_id} 保存指标 {datasource_id}({len(data.metrics)} 个)")
    return {"status": "building"}


@router.put("/{datasource_id}/relationships")
async def update_datasource_relationships(datasource_id: str, data: RelationshipsUpdateInput,
                                          user_id: str = Depends(require_admin)):
    """保存「表关系」整表替换。只重写 data_relationship,即时生效,不重建 Qdrant/ES。"""
    await _verify_passwords(user_id, datasource_id, data.user_password, data.db_password)
    async with meta_mysql_client.session() as session:
        repo = MetaDBRepository(session, datasource_id)
        async with session.begin():
            role_changes = await repo.replace_relationships([
                DataRelationship(
                    from_table=e.from_table, from_column=e.from_column,
                    to_table=e.to_table, to_column=e.to_column,
                    description=e.description, datasource_id=datasource_id,
                )
                for e in data.relationships
            ])
            # JOIN 选路参数随表关系一起存(同一事务)
            ds_repo = DatasourceRepository(session)
            await ds_repo.set_join_config(datasource_id, data.max_extra, data.k)
            # 表关系/选路改了会影响 JOIN → 元数据版本 +1,该源旧 SQL 缓存失效
            await ds_repo.bump_meta_version(datasource_id)

    # MySQL 提交后,把列 role 的变更镜像进 Qdrant payload —— 召回侧的列 role 读自 Qdrant,
    # 不同步则 LLM 对召回到的列仍看到旧 role。只 set_payload 改 role 字段,不重嵌向量(符合"不重建索引")。
    if role_changes:
        ids_by_role: dict[str, list[str]] = {}
        for col_id, role in role_changes.items():
            ids_by_role.setdefault(role, []).append(col_id)
        col_qdrant = ColumnQdrantRepository(qdrant_client.client)
        for role, col_ids in ids_by_role.items():
            await col_qdrant.set_role_by_column_ids(datasource_id, col_ids, role)

    logger.info(f"[/datasources] user_id={user_id} 更新表关系 {datasource_id}"
                f"({len(data.relationships)} 条,联动列 role {len(role_changes)} 处)")
    return {"count": len(data.relationships)}


@router.delete("/{datasource_id}")
async def delete_datasource(datasource_id: str, data: DatasourceDeleteInput,
                            user_id: str = Depends(require_admin)):
    # 破坏性操作:管理员身份 + 账号密码 + 该源数据库密码,三重确认后才删。
    await _verify_passwords(user_id, datasource_id, data.user_password, data.db_password)
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
    # 连带清掉该源的问数会话历史(在 upload 库,全员共享 → 删源即全清)
    from repositories.conversation import ConversationRepository
    from services.excel_ingest import get_session_factory
    convs_removed = 0
    Session = get_session_factory()
    async with Session() as conv_session:
        async with conv_session.begin():
            convs_removed = await ConversationRepository(conv_session).delete_by_datasource(datasource_id)
    logger.info(
        f"[/datasources] user_id={user_id} 删除数据源 {datasource_id}"
        f"(含 meta/Qdrant/ES 清理 + 会话 {convs_removed} 条)"
    )
    return {"deleted": datasource_id}
