"""
Execution Accuracy: 跑 gold SQL 和 pred SQL,比较结果集是否等价。
这是 Text2SQL 评估的金标准——即使 SQL 写法不同,只要执行结果一样就算对。

注意点:
1. 行序不影响,要做"集合等价"
2. 列名不影响——LLM 经常加别名(`COUNT(*)` vs `total_count`),只看 value
3. 列序保留(按 SELECT 顺序),因为列序通常代表用户期望
4. 浮点数 round 到 4 位避免精度差异
5. Decimal 转 float 做比较
6. None == None
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ExecutionResult:
    def __init__(self, match: bool, gold_error: str | None = None, pred_error: str | None = None,
                 gold_rows: int = 0, pred_rows: int = 0):
        self.match = match
        self.gold_error = gold_error
        self.pred_error = pred_error
        self.gold_rows = gold_rows
        self.pred_rows = pred_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "gold_error": self.gold_error,
            "pred_error": self.pred_error,
            "gold_rows": self.gold_rows,
            "pred_rows": self.pred_rows,
        }


def _normalize_value(v: Any) -> Any:
    """统一不同 driver 返回类型,方便比较。"""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return round(float(v), 4)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return v


def _row_sort_key(values: tuple[Any, ...]) -> tuple[str, ...]:
    """统一 sort key,避免 None / 数字 / 字符串混合 TypeError。"""
    return tuple("__none__" if v is None else str(v) for v in values)


def _normalize_rows(rows: Sequence[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """
    转成可比较形式:
    - 每行只保留 values(按 SELECT 列序),不要列名——LLM alias 不可靠
    - 整体按行 sort 实现行序无关
    - SQLAlchemy `result.mappings()` 返回的 dict 列序与 SELECT 一致(Python 3.7+ dict 保序)
    """
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        values = tuple(_normalize_value(v) for v in row.values())
        normalized.append(values)
    return sorted(normalized, key=_row_sort_key)


async def _run_sql(session: AsyncSession, sql: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """执行 SQL,返回 (rows, error)。出错时 rows 为 None。"""
    try:
        result = await session.execute(text(sql))
        rows = [dict(r) for r in result.mappings().fetchall()]
        return rows, None
    except Exception as exc:
        # 注意要 rollback,否则同一 session 后续语句会失败
        try:
            await session.rollback()
        except Exception:
            pass
        return None, str(exc)


async def execution_match(
    gold_sql: str,
    pred_sql: str | None,
    dw_session: AsyncSession,
) -> ExecutionResult:
    """
    比较 gold_sql 和 pred_sql 的执行结果是否等价。

    pred_sql 为 None 或空 → 直接判错(没生成 SQL)。
    """
    if not pred_sql or not pred_sql.strip():
        return ExecutionResult(match=False, pred_error="pred_sql is empty")

    gold_rows, gold_err = await _run_sql(dw_session, gold_sql)
    if gold_err is not None:
        # gold SQL 自己跑不通,这条用例本身有问题,记下来后续修
        return ExecutionResult(match=False, gold_error=gold_err)

    pred_rows, pred_err = await _run_sql(dw_session, pred_sql)
    if pred_err is not None:
        return ExecutionResult(
            match=False, pred_error=pred_err,
            gold_rows=len(gold_rows or []),
        )

    assert gold_rows is not None and pred_rows is not None
    match = _normalize_rows(gold_rows) == _normalize_rows(pred_rows)
    return ExecutionResult(
        match=match,
        gold_rows=len(gold_rows),
        pred_rows=len(pred_rows),
    )
