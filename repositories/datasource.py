"""数据源注册表的 CRUD。

密码进库前加密(core.crypto.encrypt),取连接配置时解密;
对外的 DatasourceInfo 一律不带密码。
"""
from sqlalchemy import select
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
