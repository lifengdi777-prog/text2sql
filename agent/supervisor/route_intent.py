"""意图路由节点:判断消息是「对已有结果的画图/换图请求」还是「数据查询」。

两段式,常规查询零额外开销:
  1) 关键词预筛:消息不含 图/画/可视化/chart/plot → 直接 query,不调 LLM;
  2) 命中关键词 → LLM 终判(像"画一下各地区销售额的图"这种带新查询条件的仍是 query)。
LLM 调用失败 → 兜底 query(宁可多跑一次查询,也不要拿旧数据画错图)。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.llm import llm
from agent.supervisor.schemas import SupervisorContext, SupervisorState
from core.log import logger

_PROMPT_PATH = Path(__file__).parent / "prompts" / "route.md"
_PROMPT_CACHE: str | None = None

# 预筛:不含任何绘图相关字眼的消息必然是 query,直接放行省一次 LLM 调用。
# 允许误命中(如"图书销量"含"图"),由 LLM 终判纠正。
CHART_HINT = re.compile(r"图|画|可视化|chart|plot", re.IGNORECASE)


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


class RouteDecision(BaseModel):
    route: Literal["chart", "query"]


async def _llm_route(text: str, last_question: str | None) -> str:
    """LLM 终判。独立成函数,便于测试替换。"""
    structured = llm.with_structured_output(RouteDecision, method="json_mode")
    ctx = f"上一轮问题:{last_question}" if last_question else "上一轮问题:(空,这是本会话第一条消息)"
    decision: RouteDecision = await structured.ainvoke([
        SystemMessage(content=_get_prompt()),
        SystemMessage(content=ctx),
        HumanMessage(content=text),
    ])  # type: ignore
    return decision.route


async def route_intent(state: SupervisorState, runtime: Runtime[SupervisorContext]):
    # 路由本身不发步骤事件:query 路径由子 agent 的"解析用户意图"开场,
    # chart 路径由"读取查询结果"开场,前端无感知空窗。
    text = str(state.messages[-1].content) if state.messages else ""
    if not CHART_HINT.search(text):
        return {"route": "query"}

    last_question: str | None = None
    if state.history:
        last_question = state.history[-1].get("question")
    try:
        route = await _llm_route(text, last_question)
    except Exception as exc:
        logger.warning(f"意图路由 LLM 失败,兜底走查询:{exc}")
        route = "query"
    logger.info(f"supervisor 路由 → {route}(消息:{text[:50]!r})")
    return {"route": route}
