"""用户表 Repository:users 表的查询 / 创建。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import UserMySQL


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_username(self, username: str) -> UserMySQL | None:
        stmt = select(UserMySQL).where(UserMySQL.username == username)
        return await self.session.scalar(stmt)

    async def get_by_id(self, user_id: int) -> UserMySQL | None:
        return await self.session.get(UserMySQL, user_id)

    async def create(self, username: str, password_hash: str) -> UserMySQL:
        user = UserMySQL(username=username, password_hash=password_hash)
        self.session.add(user)
        await self.session.flush()
        return user
