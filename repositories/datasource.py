"""数据源注册表的 CRUD。

密码进库前加密(core.crypto.encrypt),取连接配置时解密;
对外的 DatasourceInfo 一律不带密码。
"""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from conf.app_config import DBConfig
from core import crypto
from dtos.datasource import DatasourceCreate, DatasourceInfo
from models.datasource import DatasourceMySQL


class DatasourceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, create: DatasourceCreate) -> None:
        row = DatasourceMySQL(
            id=create.id,
            name=create.name,
            type=create.type,
            host=create.host,
            port=create.port,
            username=create.username,
            password_enc=crypto.encrypt(create.password),
            default_database=create.default_database,
            created_by=create.created_by,
            status="active",
        )
        self.session.add(row)

    async def get_by_id(self, datasource_id: str) -> DatasourceMySQL | None:
        return await self.session.scalar(
            select(DatasourceMySQL).where(DatasourceMySQL.id == datasource_id)
        )

    async def exists(self, datasource_id: str) -> bool:
        return (await self.get_by_id(datasource_id)) is not None

    async def list_all(self) -> list[DatasourceInfo]:
        rows = await self.session.scalars(select(DatasourceMySQL))
        return [DatasourceInfo.model_validate(row) for row in rows]

    async def delete(self, datasource_id: str) -> bool:
        """删除数据源注册行。返回是否删到。
        注:本方法只删 datasource 表;该源的 5 张 meta 表行 / Qdrant / ES 由调用方另行清理。"""
        ds = await self.get_by_id(datasource_id)
        if ds is None:
            return False
        await self.session.delete(ds)
        return True

    async def set_build_status(self, datasource_id: str, build_status: str,
                               table_count: int | None = None, last_error: str | None = None) -> None:
        ds = await self.get_by_id(datasource_id)
        if ds is None:
            return
        ds.build_status = build_status
        ds.last_error = last_error
        if table_count is not None:
            ds.table_count = table_count


# 幂等迁移:给已存在的 datasource 表补 build_status/last_error/table_count 列,并把存量行回填为 ready。
# datasource 表不随 init_data 重建(里面是连接源数据),所以这几列单独在应用启动时迁移补上。
async def ensure_datasource_columns(engine) -> None:
    cols = {"build_status": "VARCHAR(16) NULL", "last_error": "TEXT NULL", "table_count": "INT NULL"}
    async with engine.begin() as conn:
        existing = set((await conn.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='datasource'"
        ))).scalars().all())
        if not existing:
            return  # 表还不存在(全新环境),等 init_data 按模型建好(已含这些列)
        for name, ddl in cols.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE datasource ADD COLUMN {name} {ddl}"))
        # 存量行(已物化)回填:build_status=ready,table_count=该源 table_info 行数
        await conn.execute(text(
            "UPDATE datasource d "
            "LEFT JOIN (SELECT datasource_id, COUNT(*) c FROM table_info GROUP BY datasource_id) x "
            "ON d.id = x.datasource_id "
            "SET d.table_count = COALESCE(x.c, 0), d.build_status = 'ready' "
            "WHERE d.build_status IS NULL"
        ))

    def to_db_config(self, ds: DatasourceMySQL, database: str | None = None) -> DBConfig:
        """解密密码 + 组装成连接用的 DBConfig(供 ClientRegistry 建连接池)。

        database 不传则用数据源的 default_database。
        """
        return DBConfig(
            host=ds.host,
            port=ds.port,
            user=ds.username,
            password=crypto.decrypt(ds.password_enc),
            database=database or ds.default_database or "",
        )
