"""应用启动时幂等建库+建表:新机/新部署首次启动自动就绪,无需手动跑脚本。

- 建库:meta / wenshu(应用自有的派生/基础设施库);
- 建表:meta 库的 datasource + 5 张元数据表;wenshu 库的 users / upload_datasets / 会话表。

已存在则跳过(CREATE DATABASE/TABLE IF NOT EXISTS / checkfirst);列级变更由 ensure_*_columns 迁移补。
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from conf.app_config import app_config
from models import Base
from models.datasource import DatasourceMySQL
from models.meta import (
    ColumnInfoMySQL,
    ColumnMetricMySQL,
    DataRelationshipMySQL,
    MetricInfoMySQL,
    TableInfoMySQL,
)
from models.sql_cache import SqlCacheMySQL


# meta 库要建的表(只建这些,别在 meta 库里建出 users/会话等运营表)
_META_TABLES = [
    DatasourceMySQL.__table__,
    TableInfoMySQL.__table__,
    ColumnInfoMySQL.__table__,
    MetricInfoMySQL.__table__,
    ColumnMetricMySQL.__table__,
    DataRelationshipMySQL.__table__,
    SqlCacheMySQL.__table__,  # SQL 缓存表(新表,create_all 幂等自动建)
]


async def ensure_databases() -> None:
    """建 meta / upload 库(若不存在)。用 db_meta 的账号连到服务器(不指定库)执行 CREATE DATABASE。
    业务库(dw 等)是用户源数据,不在此创建。"""
    dbs = [app_config.db_meta.database]
    if app_config.db_wenshu is not None:
        dbs.append(app_config.db_wenshu.database)

    cfg = app_config.db_meta  # 同一台 MySQL,用 meta 的账号建库即可
    admin_uri = f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/?charset=utf8mb4"
    admin = create_async_engine(admin_uri)
    try:
        async with admin.connect() as conn:
            for db in dbs:
                await conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4"))
            await conn.commit()
    finally:
        await admin.dispose()


async def ensure_app_tables() -> None:
    """在 meta 库建 6 张元数据表;在 upload 库建用户/上传相关表(沿用 init_upload 的 create_all)。"""
    from clients.mysql import meta_mysql_client
    from services.excel_ingest import get_session_factory
    # 导入以注册到 Base.metadata,供下面 upload 库 create_all 一并建表(编辑会话/操作日志)
    from models.dataset_edit import DatasetEditOp, DatasetEditSession  # noqa: F401

    # meta 库:只建这 6 张
    async with meta_mysql_client.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_META_TABLES)

    # upload 库:建用户/上传/会话等表(create_all 幂等;与 init_upload 行为一致)
    session_factory = get_session_factory()
    async with session_factory() as session:
        conn = await session.connection()
        await conn.run_sync(Base.metadata.create_all)
        await session.commit()
