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
from models.conversation import ConversationMySQL, MessageMySQL
from models.dataset_edit import DatasetEditOp, DatasetEditSession
from models.upload import UploadDatasetMySQL
from models.user import UserMySQL


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

# wenshu 库要建的表(用户/会话/上传/编辑会话等运营数据;不要把 meta 域的表建过来)。
# 之前这里用无参 create_all,会把 Base.metadata 里所有表(含 meta 域)都在 wenshu 建成空壳,
# 易造成"看错库"的困惑。改成显式列出本库该有的表,与 _META_TABLES 同一套路。
_WENSHU_TABLES = [
    UserMySQL.__table__,
    ConversationMySQL.__table__,
    MessageMySQL.__table__,
    UploadDatasetMySQL.__table__,
    DatasetEditSession.__table__,
    DatasetEditOp.__table__,
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
    """在 meta 库建元数据表;在 wenshu 库建用户/上传/会话相关表。两边都只建本库该有的表。"""
    from clients.mysql import meta_mysql_client
    from services.excel_ingest import get_session_factory

    # meta 库:只建元数据 + SQL 缓存表
    async with meta_mysql_client.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_META_TABLES)

    # wenshu 库:只建运营数据表(显式列表,不再无参全量建,避免误建 meta 域空壳表)
    session_factory = get_session_factory()
    async with session_factory() as session:
        conn = await session.connection()
        await conn.run_sync(Base.metadata.create_all, tables=_WENSHU_TABLES)
        await session.commit()
