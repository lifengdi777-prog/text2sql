"""会话历史的 ORM:会话 + 消息两张表(建在 db_wenshu,与 users/upload_datasets 同库)。

表结构:
  conversations
    id           主键
    user_id      归属用户(所有查询强制按它过滤 → 用户间历史天然隔离)
    source       'db'(主图问数) / 'dataset'(Excel 数据集问数)
    dataset_id   仅 source='dataset' 时有值,指向 upload_datasets.id
    title        会话标题(默认取首个问题截断,可改名)
    created_at / updated_at  updated_at 每次新消息时刷新,列表按它倒序

  messages
    id              主键
    conversation_id 所属会话(index)
    role            'user' / 'assistant'
    content         用户问题文本(role='user' 用)
    payload         JSON,助手回复的完整渲染结构
                    (sql / result / chart_config / interpretation / guide_queries / status / steps),
                    打开历史即原样重现,无需重跑 SQL
    created_at
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


class ConversationMySQL(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="会话编号")
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="归属用户")
    source: Mapped[str] = mapped_column(String(16), default="db", comment="db / dataset")
    dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="数据集会话才有,指向 upload_datasets")
    # db 会话绑定的数据源(指向 meta 库 datasource.id);dataset 会话为空。
    # 删除数据源时按它连带清理会话;问数列表按它隔离(切到哪个源只看哪个源的历史)。
    datasource_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="db 会话才有,指向 datasource.id")
    title: Mapped[str] = mapped_column(String(255), default="新对话", comment="会话标题")
    # 用 Python 端 default/onupdate(本地时间)而非仅 server_default(DB 端,容器多为 UTC),
    # 避免新建/更新时间与全项目 datetime.now() 不一致,导致前端按本地日期分组错位。
    # server_default 保留,仅作建表 DDL / 裸 SQL 插入的兜底。
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        server_default=func.now(),
        onupdate=datetime.now,
    )

    # 列表查询:数据集按 (user_id, source, dataset_id);问数按 (user_id, source, datasource_id)
    __table_args__ = (
        Index("idx_user_source_dataset", "user_id", "source", "dataset_id"),
        Index("idx_user_source_ds", "user_id", "source", "datasource_id"),
    )


class MessageMySQL(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="消息编号")
    conversation_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属会话")
    role: Mapped[str] = mapped_column(String(16), comment="user / assistant")
    # 用户问题文本(assistant 消息此字段为空,内容在 payload)
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户问题文本")
    # 助手回复的完整渲染结构(MySQL 8+ 原生 JSON)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="助手回复渲染数据")
    # 同上:本地时间,与 conversations 保持一致
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
