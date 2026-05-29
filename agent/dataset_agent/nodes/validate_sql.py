"""校验 LLM 产出的 SQL(替代旧的 validate_spec)。

两道关:
  1. **安全/语法**(sqlglot,零 DB):必须是**单条 SELECT**(可含 WITH/UNION/子查询/JOIN),
     拒绝 INSERT/UPDATE/DELETE/DROP/CREATE/SET/PRAGMA 等任何非查询;通过后**包一层强制 LIMIT**。
  2. **绑定校验**(DuckDB EXPLAIN):表名/列名/语法错误会被抓出来 —— 不真正取数,只做 bind。

任一关失败 → 记 sql_issues + 置 error,交给 graph 路由到 correct_sql 重写(计数封顶后兜底)。
通过 → 把规范化(带 LIMIT)后的 SQL 写回 generated_sql,清空 issues/error 去执行。
"""
from __future__ import annotations

import sqlglot
from langgraph.runtime import Runtime
from sqlglot import exp

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.schemas import WSStepInfo
from core.log import logger
from services.duckdb_exec import ROW_LIMIT, explain_sql


def _safe_select(sql: str) -> tuple[bool, str, str | None]:
    """返回 (是否安全, 原因, 规范化后的SQL)。规范化 = 重新序列化 + 外层强制 LIMIT。"""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception as exc:
        return False, f"SQL 解析失败:{exc}", None
    if len(statements) != 1:
        return False, "只允许一条语句(检测到多条或为空)", None
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        return False, f"只允许 SELECT 查询,检测到:{type(stmt).__name__}", None
    inner = stmt.sql(dialect="duckdb")
    normalized = f'SELECT * FROM (\n{inner}\n) AS "_wrapped" LIMIT {ROW_LIMIT}'
    return True, "", normalized


async def validate_sql(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="校验查询", status="running"))

    if state.error:
        return {}

    sql = (state.generated_sql or "").strip()
    if not sql:
        issues = ["未生成 SQL"]
        writer(WSStepInfo(step="校验查询", status="error", data={"issues": issues}))
        return {"sql_issues": issues, "error": "未生成查询"}

    # 关 1:安全 + 语法 + 规范化
    ok, reason, normalized = _safe_select(sql)
    if not ok or normalized is None:
        issues = [f"SQL 不安全或非查询语句:{reason}"]
        logger.warning(f"SQL 安全校验未过:{reason}")
        writer(WSStepInfo(step="校验查询", status="error", data={"issues": issues}))
        return {"sql_issues": issues, "error": "生成的不是安全的 SELECT 查询"}

    # 没 dataset_id → 没法 EXPLAIN,放行(execute 会自然报错)
    if state.dataset_id is None:
        writer(WSStepInfo(step="校验查询", status="success"))
        return {"generated_sql": normalized, "sql_issues": [], "error": None}

    # 关 2:DuckDB 绑定校验
    try:
        await explain_sql(state.dataset_id, normalized)
    except Exception as exc:
        issues = [f"SQL 校验失败(表/列/语法):{exc}"]
        logger.warning(f"SQL 绑定校验未过:{exc}")
        writer(WSStepInfo(step="校验查询", status="error", data={"issues": issues}))
        return {"generated_sql": normalized, "sql_issues": issues, "error": f"SQL 校验失败:{exc}"}

    writer(WSStepInfo(step="校验查询", status="success"))
    return {"generated_sql": normalized, "sql_issues": [], "error": None}
