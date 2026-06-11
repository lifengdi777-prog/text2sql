"""run_query 适配器:把 db_agent / dataset_agent 包装成归因 agent 需要的统一能力。

签名统一为  async (自包含问题) -> {"rows", "sql", "error"}。

关键点:
  - 子查询走「静默执行」:自行消费子图的事件流(custom 就地丢弃、values 取末态),
    子查询的"解析意图/生成SQL/执行SQL…"细粒度事件不会冒泡污染归因的前端流
    (直接 ainvoke 会经 contextvars 冒泡,已实测;astream 自消费则完全隔离,已实测);
  - intent_pre_parsed=True:子问题是机器生成的自包含数据问题,跳过意图节点的 LLM 调用;
  - internal_subquery=True:跳过 interpret_result(只要数据不要解读,再省一次 LLM);
  - SQL 缓存照常读写:重复归因/相似维度子问题直接命中,越用越快。
"""
from __future__ import annotations

from typing import Any

from langchain.messages import HumanMessage

from agent.attribution_agent.schemas import RunQuery
from core.log import logger


async def _invoke_silently(graph, state, context) -> dict[str, Any]:
    """静默跑一张子图:吞掉 custom 事件,返回 values 流的末态(完整 state dict)。"""
    final: dict[str, Any] | None = None
    async for mode, payload in graph.astream(
        input=state, context=context, stream_mode=["custom", "values"],
    ):
        if mode == "values":
            final = payload
    return final or {}


def make_db_run_query(query_context) -> RunQuery:
    """db 入口的查询能力:question → db_agent 完整管线 → rows/sql/error。

    query_context 是该请求已构造好的 WSAgentContext(数据源/库已作用域化)。
    """
    from agent.db_agent.graph import graph as db_graph
    from agent.schemas import WSAgentState

    async def run_query(question: str) -> dict[str, Any]:
        state = WSAgentState(messages=[HumanMessage(content=question)],
                             intent_pre_parsed=True, internal_subquery=True)
        try:
            final = await _invoke_silently(db_graph, state, query_context)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"归因子查询失败(db):{question!r} -> {exc}")
            return {"rows": None, "sql": None, "error": str(exc)}
        return {"rows": final.get("sql_result"), "sql": final.get("sql"),
                "error": final.get("error")}

    return run_query


def make_dataset_run_query(user_id: str, dataset_id: int) -> RunQuery:
    """dataset 入口的查询能力:question → dataset_agent 完整管线 → rows/sql/error。"""
    from agent.dataset_agent.graph import dataset_graph
    from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState

    async def run_query(question: str) -> dict[str, Any]:
        state = DatasetAgentState(messages=[HumanMessage(content=question)],
                                  dataset_id=dataset_id,
                                  intent_pre_parsed=True, internal_subquery=True)
        try:
            final = await _invoke_silently(dataset_graph, state,
                                           DatasetAgentContext(user_id=user_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"归因子查询失败(dataset:{dataset_id}):{question!r} -> {exc}")
            return {"rows": None, "sql": None, "error": str(exc)}
        return {"rows": final.get("sql_result"), "sql": final.get("generated_sql"),
                "error": final.get("error")}

    return run_query
