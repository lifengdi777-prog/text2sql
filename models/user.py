"""用户表 ORM(放 upload 库,跟 upload_datasets 同库,复用同一个 sessionmaker)。

轻量自建登录:username 唯一 + bcrypt 密码哈希。
数据集归属(upload_datasets.user_id)存的是这里的 user.id 字符串。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from models import Base


class UserMySQL(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="用户编号")
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="登录名(唯一)")
    # bcrypt 哈希(含盐),固定 60 字符,留宽到 255 兼容未来换算法
    password_hash: Mapped[str] = mapped_column(String(255), comment="bcrypt 密码哈希")
    # 本地时间,与全项目 datetime.now() 统一;server_default 仅作兜底
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
