"""上传数据集的 ORM:一张表 + JSON 列存 schema profile。

表结构:
  upload_datasets
    id            主键
    user_id       归属用户
    name          显示名
    original_filename
    folder_path   对象存储前缀 ds_{id}(原始 Excel + 各 sheet parquet 都在此前缀下)
    status        cleaning / needs_header / indexing / ready / failed / deleting
    sheet_count
    total_rows
    schema_json   JSON 列,存每个 sheet 的列详情(类型/基数/枚举值/统计)
    created_at / updated_at
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


class UploadDatasetMySQL(Base):
    __tablename__ = "upload_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="数据集编号")
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="归属用户")
    name: Mapped[str] = mapped_column(String(255), comment="显示名")
    original_filename: Mapped[str | None] = mapped_column(String(255), comment="原始文件名")
    folder_path: Mapped[str | None] = mapped_column(String(255), comment="对象存储前缀 ds_{id}")
    status: Mapped[str] = mapped_column(String(32), default="cleaning",
                                         comment="cleaning / needs_header / indexing / ready / failed / deleting")
    sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    # 文件内容 SHA-256(hex 64 chars)。配合 user_id + original_filename 做去重
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="SHA-256 of original file")
    # 嵌套的 schema profile 直接放 JSON 列(MySQL 8+ 原生 JSON 类型)
    schema_json: Mapped[dict | None] = mapped_column(JSON, comment="所有 sheet 的列详情")
    # 本地时间(Python 端 default/onupdate),与全项目 datetime.now() 统一;server_default 仅作兜底
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        server_default=func.now(),
        onupdate=datetime.now,
    )

    # 联合索引,加速"同用户 + 同名 + 同 hash"查重
    __table_args__ = (
        Index("idx_user_name_hash", "user_id", "original_filename", "content_hash"),
    )
