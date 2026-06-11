"""意图路由节点:判断消息类型,并对查询请求做多轮改写(指代消解)。

两段式,常规查询零额外开销:
  1) 关键词预筛:消息不含 图/画/可视化/chart/plot → 直接 query,不调 LLM,
     改写与守门交给子 agent 的意图节点(行为与接入 supervisor 前完全一致);
  2) 命中关键词 → 一次 LLM 调用同时完成「chart / query / other 分流 + 多轮改写」:
     - chart → chart_agent;
     - query → 改写后的自包含问题原地替换 messages[-1] 并置 intent_pre_parsed,
       子图的意图节点据此短路 —— 不再重复调用 LLM(省一次调用、降低首 token 延迟);
     - other(闲聊/与数据无关)→ 不短路,交子 agent 的完整意图节点处理
       (它有领域上下文,能做更准的守门 + 生成贴合本源的引导问题)。
LLM 调用失败 → 兜底 query 且不短路(子图完整把关),不冲断流。
"""
from __future__ import annotations

import json
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
    route: Literal["chart", "query", "other"]
    # 多轮改写:route=query 时为自包含问题(首轮=原消息);chart/other 留空
    standalone_query: str = ""


def _render_history(history: list[dict] | None) -> str:
    """最近几轮(问题 + SQL + 结果快照)渲染成文本,供 LLM 做指代消解与路由判断。"""
    if not history:
        return "(无历史对话,这是用户的第一轮提问)"
    blocks: list[str] = []
    for i, t in enumerate(history, 1):
        seg = [f"## 第 {i} 轮", f"用户问题:{t.get('question', '')}"]
        if t.get("sql"):
            seg.append(f"该轮 SQL:{t['sql']}")
        rows = t.get("rows") or []
        seg.append(
            f"结果快照(前 {len(rows)} 行):{json.dumps(rows, ensure_ascii=False, default=str)}"
            if rows else "结果快照:(无)"
        )
        blocks.append("\n".join(seg))
    return "\n\n".join(blocks)


async def _llm_route(text: str, history: list[dict] | None) -> RouteDecision:
    """LLM 终判:分流 + 多轮改写,一次调用。独立成函数,便于测试替换。"""
    structured = llm.with_structured_output(RouteDecision, method="json_mode")
    return await structured.ainvoke([  # type: ignore
        SystemMessage(content=_get_prompt()),
        SystemMessage(content="# 对话历史(最近几轮,供路由判断与多轮改写用)\n"
                      + _render_history(history)),
        HumanMessage(content=text),
    ])


async def route_intent(state: SupervisorState, runtime: Runtime[SupervisorContext]):
    # 路由本身不发步骤事件:query 路径由子 agent 的意图节点开场,
    # chart 路径由"读取查询结果"开场,前端无感知空窗。
    text = str(state.messages[-1].content) if state.messages else ""
    if not CHART_HINT.search(text):
        return {"route": "query"}

    try:
        decision = await _llm_route(text, state.history)
    except Exception as exc:
        logger.warning(f"意图路由 LLM 失败,兜底走查询(子图完整把关):{exc}")
        return {"route": "query"}

    if decision.route == "chart":
        logger.info(f"supervisor 路由 → chart(消息:{text[:50]!r})")
        return {"route": "chart"}

    if decision.route == "query" and decision.standalone_query.strip():
        # 改写后的自包含问题同 id 原地替换(add_messages 见同 id 替换而非追加),
        # 子图意图节点据 intent_pre_parsed 短路,本轮只有这一次意图 LLM 调用。
        orig = state.messages[-1]
        logger.info(f"supervisor 路由 → query(已改写,子图意图节点短路):"
                    f"{decision.standalone_query[:50]!r}")
        return {
            "route": "query",
            "intent_pre_parsed": True,
            "messages": [HumanMessage(content=decision.standalone_query, id=orig.id)],
        }

    # other / query-但没给出改写 → 不短路,交子 agent 完整意图节点把关
    logger.info(f"supervisor 路由 → query(完整意图判定,LLM 初判={decision.route})")
    return {"route": "query"}
