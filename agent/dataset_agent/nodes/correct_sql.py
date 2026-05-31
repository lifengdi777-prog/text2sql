"""根据 validate_sql 报的 issues 让 LLM 重写 SQL。

复用 generate_sql 的 system prompt(sql_generator.md)和 SQLDraft 输出结构,
只把 user content 换成「修正语境」:真实 schema + 上一版 SQL + 校验报告 + 用户原问题。
最多重试 MAX_RETRY 次(sql_retry_count 计数封顶),用尽后由 graph 兜底。
"""
from __future__ import annotations

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.dataset_agent.nodes import latest_user_query
from agent.dataset_agent.nodes.generate_sql import SQLDraft, _get_prompt
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger

MAX_RETRY = 2  # 最多让 LLM 重写 2 次,避免死循环


def _build_correct_message(query: str, rendered_schema: str, last_sql: str, issues: list[str]) -> str:
    issues_text = "\n".join(f"- {x}" for x in issues)
    return (
        f"# 数据集 Schema\n{rendered_schema}\n\n"
        f"# 你上一轮写的 SQL(有问题)\n{last_sql}\n\n"
        f"# 校验报告(请逐条修复)\n{issues_text}\n\n"
        f"# 用户原始问题\n{query}\n\n"
        f"请输出修正后的**完整**一条 DuckDB SELECT(JSON)。"
        f"所有 sheet 和列名必须严格使用 schema 中真实存在的名字,且用双引号包。"
    )


async def correct_sql(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    retry_count = state.sql_retry_count + 1
    writer(WSStepInfo(step=f"修正查询(第 {retry_count} 次)", status="running"))

    query = latest_user_query(state.messages) or ""
    issues = state.sql_issues or []
    last_sql = state.generated_sql or ""

    structured = llm.with_structured_output(SQLDraft, method="json_mode")
    try:
        draft: SQLDraft = await structured.ainvoke([  # type: ignore
            SystemMessage(content=_get_prompt()),
            HumanMessage(content=_build_correct_message(str(query), state.rendered_schema, last_sql, issues)),
        ])
        sql = (draft.sql or "").strip()
        logger.info(f"SQL 第 {retry_count} 次修正:{sql} | reason={draft.reason}")
        writer(WSStepInfo(
            step=f"修正查询(第 {retry_count} 次)", status="success",
            data={"sql": sql, "reason": draft.reason},
        ))
        # 清空 issues/error,带新 SQL 回到 validate 重判
        return {"generated_sql": sql, "sql_retry_count": retry_count, "sql_issues": [], "error": None}
    except Exception as exc:
        # 修正本身崩了:计数照样 +1,回 validate 会再判一次,达上限即兜底
        logger.exception(f"SQL 修正异常:{exc}")
        writer(WSStepInfo(step=f"修正查询(第 {retry_count} 次)", status="error", data={"error": str(exc)}))
        return {"sql_retry_count": retry_count, "sql_issues": [], "error": None}
