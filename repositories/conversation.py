"""会话历史 Repository:conversations / messages 两张表的 CRUD。

所有"按用户"的查询强制带 user_id 过滤,用户间历史天然隔离。
payload 是 MySQL JSON 列,SQLAlchemy 返回已反序列化的 dict,写入直接传 dict。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation import ConversationMySQL, MessageMySQL


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: str,
        source: str,
        title: str,
        dataset_id: int | None = None,
    ) -> ConversationMySQL:
        # created_at/updated_at 由模型层 default=datetime.now(本地时间)自动填充,无需在此显式赋值。
        conv = ConversationMySQL(
            user_id=user_id,
            source=source,
            dataset_id=dataset_id,
            title=title[:255] or "新对话",
        )
        self.session.add(conv)
        await self.session.flush()
        return conv

    async def get(self, conversation_id: int) -> ConversationMySQL | None:
        return await self.session.get(ConversationMySQL, conversation_id)

    async def get_owned(self, conversation_id: int, user_id: str) -> ConversationMySQL | None:
        """取会话并校验归属:不存在或不属于当前用户都返回 None(由调用方转 404)。"""
        conv = await self.session.get(ConversationMySQL, conversation_id)
        if conv is None or conv.user_id != user_id:
            return None
        return conv

    async def list_by_user(
        self,
        user_id: str,
        source: str,
        dataset_id: int | None = None,
    ) -> list[ConversationMySQL]:
        """列出当前用户在某来源下的会话(最近更新优先)。

        source='db'      → 主图问数历史(忽略 dataset_id)
        source='dataset' → 指定 dataset_id 的数据集问数历史
        """
        stmt = select(ConversationMySQL).where(
            ConversationMySQL.user_id == user_id,
            ConversationMySQL.source == source,
        )
        if source == "dataset" and dataset_id is not None:
            stmt = stmt.where(ConversationMySQL.dataset_id == dataset_id)
        stmt = stmt.order_by(ConversationMySQL.updated_at.desc(), ConversationMySQL.id.desc())
        return list((await self.session.scalars(stmt)).all())

    async def rename(self, conversation_id: int, title: str) -> None:
        conv = await self.session.get(ConversationMySQL, conversation_id)
        if conv is not None:
            conv.title = (title[:255] or conv.title)

    async def touch(self, conversation_id: int) -> None:
        """碰一下 updated_at(新消息后调用,让会话冒泡到列表顶部)。"""
        conv = await self.session.get(ConversationMySQL, conversation_id)
        if conv is not None:
            # 显式赋值才会被标记为 dirty 并发出 UPDATE(赋相同值不会触发 onupdate)
            conv.updated_at = datetime.now()

    async def delete(self, conversation_id: int) -> None:
        """删会话 + 其下所有消息。"""
        await self.session.execute(
            delete(MessageMySQL).where(MessageMySQL.conversation_id == conversation_id)
        )
        await self.session.execute(
            delete(ConversationMySQL).where(ConversationMySQL.id == conversation_id)
        )

    # ── 消息 ────────────────────────────────────────────────
    async def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> MessageMySQL:
        msg = MessageMySQL(
            conversation_id=conversation_id,
            role=role,
            content=content,
            payload=payload,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def list_messages(self, conversation_id: int) -> list[MessageMySQL]:
        stmt = (
            select(MessageMySQL)
            .where(MessageMySQL.conversation_id == conversation_id)
            .order_by(MessageMySQL.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())
