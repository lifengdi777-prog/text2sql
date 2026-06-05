"""编辑会话 / 操作日志的 Repository:dataset_edit_sessions + dataset_edit_ops 的 CRUD。

所有"按会话"的操作都可带 user_id 校验归属(用户间天然隔离)。
重放只取 active=True 的 op(按 seq 升序);撤销 = 把最后一条 active op 置 False。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.dataset_edit import DatasetEditOp, DatasetEditSession


class DatasetEditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 会话 ────────────────────────────────────────────────
    async def get_or_create_active(self, dataset_id: int, user_id: str) -> DatasetEditSession:
        """取该数据集当前活跃会话;没有则新建(单活跃,决策 4)。

        已有活跃会话直接复用(接管),不新建 —— 同一数据集同时只存在一个活跃编辑会话。
        """
        stmt = select(DatasetEditSession).where(
            DatasetEditSession.dataset_id == dataset_id,
            DatasetEditSession.status == "active",
        ).limit(1)
        existing = await self.session.scalar(stmt)
        if existing is not None:
            return existing
        sess = DatasetEditSession(
            dataset_id=dataset_id, user_id=user_id,
            status="active", active_marker=dataset_id,
        )
        self.session.add(sess)
        await self.session.flush()
        return sess

    async def get_owned(self, session_id: int, user_id: str) -> DatasetEditSession | None:
        """取会话并校验归属:不存在或不属于当前用户 → None(调用方转 404)。"""
        sess = await self.session.get(DatasetEditSession, session_id)
        if sess is None or sess.user_id != user_id:
            return None
        return sess

    async def discard(self, session_id: int) -> None:
        """丢弃会话:status=discarded + 清 active_marker(释放该数据集的活跃名额)。"""
        sess = await self.session.get(DatasetEditSession, session_id)
        if sess is not None:
            sess.status = "discarded"
            sess.active_marker = None

    async def touch(self, session_id: int) -> None:
        sess = await self.session.get(DatasetEditSession, session_id)
        if sess is not None:
            sess.updated_at = datetime.now()

    # ── 操作日志 ─────────────────────────────────────────────
    async def next_seq(self, session_id: int) -> int:
        """下一个 seq(当前最大 seq + 1,空则 1)。包含已撤销的 op,保证 seq 单调不复用。"""
        stmt = select(DatasetEditOp.seq).where(
            DatasetEditOp.session_id == session_id
        ).order_by(DatasetEditOp.seq.desc()).limit(1)
        last = await self.session.scalar(stmt)
        return (last or 0) + 1

    async def add_op(
        self, session_id: int, *, nl: str | None, sql: str | None, op_type: str,
        target_sheet: str | None = None, affected: dict[str, Any] | None = None,
    ) -> DatasetEditOp:
        op = DatasetEditOp(
            session_id=session_id, seq=await self.next_seq(session_id),
            nl=nl, sql=sql, op_type=op_type, target_sheet=target_sheet,
            affected=affected, active=True,
        )
        self.session.add(op)
        await self.session.flush()
        return op

    async def list_ops(self, session_id: int, active_only: bool = False) -> list[DatasetEditOp]:
        """按 seq 升序列出 op;active_only=True 只取生效的(重放用)。"""
        stmt = select(DatasetEditOp).where(DatasetEditOp.session_id == session_id)
        if active_only:
            stmt = stmt.where(DatasetEditOp.active.is_(True))
        stmt = stmt.order_by(DatasetEditOp.seq.asc())
        return list((await self.session.scalars(stmt)).all())

    async def active_sql(self, session_id: int) -> list[str]:
        """重放用:生效 op 的 SQL 列表(按 seq 升序,跳过空 SQL)。"""
        ops = await self.list_ops(session_id, active_only=True)
        return [o.sql for o in ops if o.sql]

    async def undo_last(self, session_id: int) -> DatasetEditOp | None:
        """撤销:把 seq 最大的生效 op 置 active=False,返回它;无可撤销则 None。"""
        stmt = select(DatasetEditOp).where(
            DatasetEditOp.session_id == session_id,
            DatasetEditOp.active.is_(True),
        ).order_by(DatasetEditOp.seq.desc()).limit(1)
        op = await self.session.scalar(stmt)
        if op is None:
            return None
        op.active = False
        return op

    async def delete_by_dataset(self, dataset_id: int) -> int:
        """删数据集时连带清理:删该数据集所有会话及其 op。返回删除的会话数。"""
        sess_ids = list((await self.session.scalars(
            select(DatasetEditSession.id).where(DatasetEditSession.dataset_id == dataset_id)
        )).all())
        if not sess_ids:
            return 0
        await self.session.execute(
            delete(DatasetEditOp).where(DatasetEditOp.session_id.in_(sess_ids))
        )
        res = await self.session.execute(
            delete(DatasetEditSession).where(DatasetEditSession.dataset_id == dataset_id)
        )
        return res.rowcount or 0
