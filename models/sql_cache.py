"""SQL 缓存表 ORM(放 meta 库,与 datasource/元数据同库)。

存「问题 → 已验证成功的 SQL」,命中后跳过大模型生成、只重新查库(数据实时)。
只存干净成功的路径(校验+执行都过、非澄清打断),且按数据源 + meta_version 隔离/失效。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


class SqlCacheMySQL(Base):
    __tablename__ = "sql_cache"

    # sha256 hex(归一化问题 + datasource_id + database + meta_version),定长 64
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True, comment="缓存键(sha256)")
    # 冗余存数据源,供「删数据源连带清缓存」按它批量删
    datasource_id: Mapped[str] = mapped_column(String(64), index=True, comment="所属数据源")
    # 写入时的元数据版本(也已编进 cache_key);仅供排查/统计
    meta_version: Mapped[int] = mapped_column(Integer, comment="写入时的元数据版本")
    # 归一化后的 standalone_query,仅供人工排查命中情况,不参与匹配(匹配看 cache_key)
    question: Mapped[str] = mapped_column(Text, comment="归一化后的自包含问题(排查用)")
    sql: Mapped[str] = mapped_column(Text, comment="该问题对应的、已验证成功的 SQL")
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="命中次数")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近一次命中时间")
