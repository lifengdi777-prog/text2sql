"""LLM 生成 DuckDB SQL 的节点(替代旧的 generate_spec)。

输入(从 state 拼 prompt):
  - 用户问题(messages 中最后一条 HumanMessage)
  - 数据集 schema markdown(state.rendered_schema,已含上传时算好的「可分析画像」)
  - ES 召回的真实值(state.value_hits)

输出:
  - state.generated_sql(一条 SELECT 字符串)

指标口径不再注入外部指标库,依赖 LLM 自身能力 + schema 里的可分析画像
(可派生指标公式)+ system prompt 里的通用规则(比率分子/分母、count vs distinct 等)。
"""
from __future__ import annotations

from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.dataset_agent.nodes import latest_user_query
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger

_PROMPT_REL = Path("agent/dataset_agent/prompts/sql_generator.md")
_PROMPT_CACHE: str | None = None


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_REL.read_text(encoding="utf-8")
    return _PROMPT_CACHE


class SQLDraft(BaseModel):
    sql: str
    reason: str = ""


def _format_value_hits(hits: list[dict]) -> str:
    if not hits:
        return "(本次问题无 ES 召回结果)"
    lines = ["格式:- [sheet] col = value"]
    for h in hits:
        lines.append(f"- [{h.get('sheet','?')}] {h.get('col','?')} = {h.get('value','?')}")
    return "\n".join(lines)


async def generate_sql(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成查询", status="running"))

    if state.error:
        return {}

    query = latest_user_query(state.messages)
    if not query:
        msg = "用户问题为空"
        writer(WSStepInfo(step="生成查询", status="error", data={"error": msg}))
        return {"error": msg}

    system_prompt = _get_prompt()
    hits_md = _format_value_hits(state.value_hits)
    user_content = (
        f"# 数据集 Schema\n{state.rendered_schema}\n\n"
        f"# 用户问题里疑似涉及的真实值(ES 召回,若有 → WHERE 优先用这些精确值)\n{hits_md}\n\n"
        f"# 用户问题\n{query}\n\n"
        f"请输出一条 DuckDB SELECT(JSON 格式)。**所有 sheet/列名必须是 schema 里真实存在的,且用双引号包**。"
    )

    structured = llm.with_structured_output(SQLDraft, method="json_mode")
    try:
        draft: SQLDraft = await structured.ainvoke([  # type: ignore
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])
        sql = (draft.sql or "").strip()
        logger.info(f"生成 SQL:{sql} | reason={draft.reason}")
        writer(WSStepInfo(step="生成查询", status="success", data={"sql": sql, "reason": draft.reason}))
        return {"generated_sql": sql}
    except Exception as exc:
        logger.exception(f"SQL 生成失败:{exc}")
        writer(WSStepInfo(step="生成查询", status="error", data={"error": str(exc)}))
        return {"error": f"生成查询失败:{exc}"}
