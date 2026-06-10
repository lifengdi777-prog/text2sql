"""SQL 缓存表的读写。

约定:本仓库只负责对 session 增删改对象,提交(commit)由调用方按自己的事务边界控制
(与 ConversationRepository 等保持一致的用法)。
"""
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql_cache import SqlCacheMySQL


class SqlCacheRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_sql(self, cache_key: str) -> str | None:
        """按键取 SQL;命中则顺手累加命中次数 + 记录命中时间(调用方负责 commit)。"""
        row = await self.session.get(SqlCacheMySQL, cache_key)
        if row is None:
            return None
        row.hit_count = (row.hit_count or 0) + 1
        row.last_hit_at = datetime.now()
        return row.sql

    async def put(self, cache_key: str, datasource_id: str, meta_version: int,
                  question: str, sql: str) -> None:
        """写入/覆盖缓存。已存在则覆盖 SQL(自愈:坏 SQL 被重新生成的好 SQL 覆盖)。"""
        row = await self.session.get(SqlCacheMySQL, cache_key)
        if row is None:
            self.session.add(SqlCacheMySQL(
                cache_key=cache_key, datasource_id=datasource_id,
                meta_version=meta_version, question=question, sql=sql,
            ))
        else:
            row.sql = sql
            row.meta_version = meta_version

    async def delete(self, cache_key: str) -> None:
        """删单条(命中的缓存校验失败时,清掉这条过期缓存)。"""
        row = await self.session.get(SqlCacheMySQL, cache_key)
        if row is not None:
            await self.session.delete(row)

    async def delete_by_datasource(self, datasource_id: str) -> int:
        """删某数据源名下的全部缓存(删数据源时连带清理)。返回删除条数。"""
        res = await self.session.execute(
            delete(SqlCacheMySQL).where(SqlCacheMySQL.datasource_id == datasource_id)
        )
        return res.rowcount or 0
