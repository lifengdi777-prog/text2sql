"""数据源 ORM(放 meta 库,与 table_info 等元数据同库)。

这是多数据源的"连接注册表":存每个数据源的连接信息(密码加密)。
它是这一层唯一长期保管的源数据;5 张 meta 表都是由它 + DW introspect 派生出来的。
created_by 存创建人 user.id(连接的归属/管理权,与"指标库级共享"无关)。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


class DatasourceMySQL(Base):
    __tablename__ = "datasource"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="数据源ID,如 ds_default")
    name: Mapped[str] = mapped_column(String(128), comment="展示名")
    type: Mapped[str] = mapped_column(String(32), default="mysql", comment="类型(先只 mysql,预留 pg 等)")
    host: Mapped[str] = mapped_column(String(255), comment="主机")
    port: Mapped[int] = mapped_column(Integer, comment="端口")
    username: Mapped[str] = mapped_column(String(128), comment="连接账号")
    password_enc: Mapped[str] = mapped_column(String(512), comment="加密后的连接密码(Fernet),不存明文")
    default_database: Mapped[str | None] = mapped_column(String(128), comment="默认库名")
    created_by: Mapped[int | None] = mapped_column(Integer, comment="创建人 user.id(连接归属)")
    status: Mapped[str] = mapped_column(String(16), default="active", comment="active/disabled")
    # 接入构建状态:pending(刚注册)/building(草稿+物化中)/ready(可问数)/failed
    build_status: Mapped[str] = mapped_column(String(16), default="pending", comment="pending/building/ready/failed")
    last_error: Mapped[str | None] = mapped_column(Text, comment="构建失败原因")
    table_count: Mapped[int | None] = mapped_column(Integer, comment="已物化的表数")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
