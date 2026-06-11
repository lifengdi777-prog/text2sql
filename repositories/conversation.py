"""会话历史 Repository:conversations / messages 两张表的 CRUD。

所有"按用户"的查询强制带 user_id 过滤,用户间历史天然隔离。
payload 是 MySQL JSON 列,SQLAlchemy 返回已反序列化的 dict,写入直接传 dict。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text
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
        datasource_id: str | None = None,
    ) -> ConversationMySQL:
        # created_at/updated_at 由模型层 default=datetime.now(本地时间)自动填充,无需在此显式赋值。
        conv = ConversationMySQL(
            user_id=user_id,
            source=source,
            dataset_id=dataset_id,
            datasource_id=datasource_id,
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
        datasource_id: str | None = None,
    ) -> list[ConversationMySQL]:
        """列出当前用户在某来源下的会话(最近更新优先)。

        source='db'      → 某数据源的问数历史(给了 datasource_id 就按它隔离)
        source='dataset' → 指定 dataset_id 的数据集问数历史
        """
        stmt = select(ConversationMySQL).where(
            ConversationMySQL.user_id == user_id,
            ConversationMySQL.source == source,
        )
        if source == "dataset" and dataset_id is not None:
            stmt = stmt.where(ConversationMySQL.dataset_id == dataset_id)
        # 问数会话绑数据源:切到哪个源只看哪个源的历史(老的未归属会话 datasource_id 为空,不在此显示)
        if source == "db" and datasource_id is not None:
            stmt = stmt.where(ConversationMySQL.datasource_id == datasource_id)
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

    async def delete_by_datasource(self, datasource_id: str) -> int:
        """删除某数据源下「所有用户」的问数会话及其消息(数据源是全员共享的,删源即全清)。
        返回删除的会话数。删数据源时由 delete_datasource 调用。"""
        sub = select(ConversationMySQL.id).where(ConversationMySQL.datasource_id == datasource_id)
        # 先删消息(无 DB 外键级联,手动按子查询删),再删会话本身
        await self.session.execute(
            delete(MessageMySQL).where(MessageMySQL.conversation_id.in_(sub))
        )
        res = await self.session.execute(
            delete(ConversationMySQL).where(ConversationMySQL.datasource_id == datasource_id)
        )
        return res.rowcount or 0

    async def delete_by_dataset(self, dataset_id: int) -> int:
        """删除某数据集的会话及其消息。返回删除的会话数。删数据集时由 delete_dataset 调用。"""
        sub = select(ConversationMySQL.id).where(
            ConversationMySQL.source == "dataset",
            ConversationMySQL.dataset_id == dataset_id,
        )
        await self.session.execute(
            delete(MessageMySQL).where(MessageMySQL.conversation_id.in_(sub))
        )
        res = await self.session.execute(
            delete(ConversationMySQL).where(
                ConversationMySQL.source == "dataset",
                ConversationMySQL.dataset_id == dataset_id,
            )
        )
        return res.rowcount or 0

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

    async def update_message_chart(
        self, conversation_id: int, message_id: int, chart_config: dict[str, Any] | None
    ) -> bool:
        """把某条 assistant 消息的 chart_config 写进 payload(前端按需出图后回写,落进历史)。

        消息不存在 / 不属于该会话 / 非 assistant → 返回 False(调用方转 404)。
        """
        msg = await self.session.get(MessageMySQL, message_id)
        if msg is None or msg.conversation_id != conversation_id or msg.role != "assistant":
            return False
        # 重新赋一个新 dict 才会被标记 dirty 触发 UPDATE(原地改 JSON 列不会被 SQLAlchemy 感知)
        payload = dict(msg.payload or {})
        payload["chartConfig"] = chart_config
        msg.payload = payload
        return True

    async def list_messages(self, conversation_id: int) -> list[MessageMySQL]:
        stmt = (
            select(MessageMySQL)
            .where(MessageMySQL.conversation_id == conversation_id)
            .order_by(MessageMySQL.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def load_recent_turns(
        self,
        conversation_id: int,
        max_turns: int = 3,
        max_rows: int = 20,
    ) -> list[dict[str, Any]]:
        """加载最近 max_turns 轮历史,供多轮改写(指代消解)用。

        把消息按「user → 紧邻的 assistant」配对成一轮,每轮取:
          - question:该轮用户问题文本
          - sql:assistant payload 里真正执行的 SQL(含 region='华东' 这类筛选,换参数型追问靠它)
          - rows:结果前 max_rows 行(按展示顺序,位置型/名称型追问靠它取值)

        注意:当前这轮的 user 消息此刻还没有 assistant 回复(stream_with_history 先落 user、
        跑完才落 assistant),配对时落单 → 自动被排除,不会把"当前问题"当成历史。
        """
        msgs = await self.list_messages(conversation_id)
        turns: list[dict[str, Any]] = []
        i = 0
        while i < len(msgs):
            cur = msgs[i]
            nxt = msgs[i + 1] if i + 1 < len(msgs) else None
            if cur.role == "user" and nxt is not None and nxt.role == "assistant":
                payload = nxt.payload or {}
                turns.append({
                    "question": cur.content,
                    "sql": payload.get("sql"),
                    "rows": (payload.get("result") or [])[:max_rows],
                })
                i += 2
            else:
                # 落单消息(如当前轮还没回复的 user、或异常半截轮)跳过
                i += 1
        return turns[-max_turns:]

    async def load_last_result(self, conversation_id: int) -> dict[str, Any] | None:
        """找最近一条带非空结果行的 assistant 消息,供「对话内画图」取数。

        payload["result"] 存的是执行后的全量行(MAX_RESULT_ROWS 截断内),与前端
        点「生成图表」按钮回传给 /chart 的 rows 完全一致,无需重跑 SQL。
        图表轮/失败轮/引导轮的 result 为空 → 跳过继续往前找;整个会话都没有 → None。

        返回 {question, sql, rows, chart_config}:
          - question:该轮配对的用户问题(往前最近的 user 消息,作图表标题/选型上下文)
          - sql:该轮真正执行的 SQL(画图出错降级 error 卡时展示来源)
          - chart_config:该轮已生成过的图表配置(留给"换图快通道":直接换类型不重选型)
        """
        msgs = await self.list_messages(conversation_id)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            if msg.role != "assistant":
                continue
            payload = msg.payload or {}
            rows = payload.get("result") or []
            if not rows:
                continue
            question: str | None = None
            for j in range(i - 1, -1, -1):
                if msgs[j].role == "user":
                    question = msgs[j].content
                    break
            return {
                "question": question,
                "sql": payload.get("sql"),
                "rows": rows,
                "chart_config": payload.get("chartConfig"),
            }
        return None


# 幂等迁移:给已存在的 conversations 表补 datasource_id 列(问数会话绑数据源用)。
# conversations 在 upload 库,create_all 只建新表不改旧表,故启动时单独 ALTER 补列。
# 老的存量 db 会话该列留 NULL(未归属任何源),按数据源过滤列表时不显示,也不会被连带删除。
async def ensure_conversation_columns() -> None:
    from services.excel_ingest import get_session_factory

    Session = get_session_factory()
    async with Session() as session:
        existing = set((await session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='conversations'"
        ))).scalars().all())
        if not existing or "datasource_id" in existing:
            return  # 表还没建(全新环境,create_all 会按模型建好)或已有该列 → no-op
        await session.execute(text("ALTER TABLE conversations ADD COLUMN datasource_id VARCHAR(64) NULL"))
        await session.execute(text(
            "ALTER TABLE conversations ADD INDEX idx_user_source_ds (user_id, source, datasource_id)"
        ))
        await session.commit()
