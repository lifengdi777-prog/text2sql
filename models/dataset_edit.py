"""「智能助手」编辑会话 + 操作日志的 ORM(建在 db_upload,与 upload_datasets 同库)。

设计见 docs/dataset_edit_agent_design.md §9:
  dataset_edit_sessions  一次性编辑工作区(每数据集至多一个活跃会话)
  dataset_edit_ops       操作日志(状态真相;重放按 seq;撤销=置 active=False)

编辑结果"下载即终点"(决策 8):op 日志只服务会话内重放/撤销,会话结束即可丢,
永不回写 canonical parquet / schema / ES。
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


class DatasetEditSession(Base):
    __tablename__ = "dataset_edit_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="编辑会话编号")
    dataset_id: Mapped[int] = mapped_column(Integer, index=True, comment="源数据集 upload_datasets.id")
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="归属用户")
    status: Mapped[str] = mapped_column(String(16), default="active", comment="active / discarded")
    # 单活跃约束(决策 4):active 时 = dataset_id,否则 NULL。
    # MySQL 唯一索引允许多个 NULL,故"每数据集至多一个活跃会话"由 active_marker 唯一性保证;
    # 已丢弃会话 marker=NULL,不参与唯一性。
    active_marker: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="active 时=dataset_id,否则空")
    # 本地时间(与全项目 datetime.now() 统一),server_default 仅作建表/裸 SQL 兜底
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, server_default=func.now(), onupdate=datetime.now,
    )

    __table_args__ = (
        UniqueConstraint("active_marker", name="uq_active_session_per_dataset"),
        Index("idx_dataset_status", "dataset_id", "status"),
    )


class DatasetEditOp(Base):
    __tablename__ = "dataset_edit_ops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="操作编号")
    session_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属编辑会话")
    seq: Mapped[int] = mapped_column(Integer, comment="会话内顺序号,重放按它升序")
    nl: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用户自然语言原文")
    sql: Mapped[str | None] = mapped_column(Text, nullable=True, comment="校验通过后真正应用的 DML/DDL")
    op_type: Mapped[str] = mapped_column(String(16), default="", comment="insert/update/delete/alter/select/mixed")
    target_sheet: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="作用的 sheet")
    affected: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="变更摘要 {count, sample, ...}")
    # 撤销 = 置 False;重放只取 active=True 的 op(按 seq)
    active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否生效(撤销则 False)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())

    __table_args__ = (
        Index("idx_session_seq", "session_id", "seq"),
    )
