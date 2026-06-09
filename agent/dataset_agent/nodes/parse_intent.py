"""意图识别节点(数据集分析专用,比主 DW 图宽松)。

目的:挡住「你好」这类与数据无关的纯闲聊,别让它们空跑整条计算管线、
最后返回一句莫名其妙的「当前数据共包含 N 条记录」。

宽松策略:只要跟「分析这份表格」沾边就放行;只有纯问候 / 闲聊才拦下。
拦下时基于已加载的 schema 给几条可点击的示例问题(guide_queries)引导用户。

放在 load_schema 之后:这样能拿到 state.rendered_schema,生成的示例问题能用真实列名。
"""
from __future__ import annotations

from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.dataset_agent.nodes import latest_user_query
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.llm import fast_llm
from agent.schemas import WSStepInfo
from core.log import logger

_PROMPT_REL = Path("agent/dataset_agent/prompts/intent_classifier.md")
_PROMPT_CACHE: str | None = None


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_REL.read_text(encoding="utf-8")
    return _PROMPT_CACHE


class IntentResult(BaseModel):
    # 多轮:把当前追问结合历史改写成的自包含问题(下游召回/SQL 只认这句);首轮/新话题=原问题。
    standalone_query: str = ""
    should_continue: bool
    guide_queries: list[str] = []


def _render_history(history: list[dict] | None) -> str:
    """把最近几轮(问题 + SQL + 结果快照)渲染成文本,供 LLM 做指代消解。"""
    import json
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


async def parse_intent(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    # load_schema 已出错:不插手,放行让后续 chart_subgraph 照常出 error 卡。
    if state.error:
        return {"should_continue": True}

    writer = runtime.stream_writer
    writer(WSStepInfo(step="意图识别", status="running"))

    query = latest_user_query(state.messages)
    if not query:
        writer(WSStepInfo(
            step="意图识别", status="success",
            data={"should_continue": False}, guide_queries=[], finish=True,
        ))
        return {"should_continue": False}

    try:
        structured = fast_llm.with_structured_output(IntentResult, method="json_mode")
        # schema / 历史各作为一条 SystemMessage 注入(不塞进 messages,避免 prompt 大括号转义问题);
        # 当前追问仍是 state.messages 最后一条。LLM 据历史把追问改写成自包含问题 standalone_query。
        result: IntentResult = await structured.ainvoke([  # type: ignore
            SystemMessage(content=_get_prompt()),
            SystemMessage(content=f"# 数据集 Schema\n{state.rendered_schema}"),
            SystemMessage(content="# 对话历史(最近几轮,供多轮改写指代消解用)\n"
                          + _render_history(state.history)),
        ] + list(state.messages))

        logger.info(f"意图识别:should_continue={result.should_continue} "
                    f"standalone={result.standalone_query!r} guides={len(result.guide_queries)}")
        writer(WSStepInfo(
            step="意图识别",
            status="success",
            data={"should_continue": result.should_continue,
                  "standalone_query": result.standalone_query},
            guide_queries=result.guide_queries,
            # 闲聊命中(不继续)→ finish=True,前端据此收尾并展示 guide_queries
            finish=not result.should_continue,
        ))
        update: dict = {
            "should_continue": result.should_continue,
            "guide_queries": result.guide_queries,
        }
        # 改写成功 → 用自包含问题原地替换 messages[-1](同 id,add_messages 替换而非追加),
        # 下游召回/SQL/解读全部拿到完整问题,零改动。
        if result.should_continue and result.standalone_query.strip():
            orig = state.messages[-1]
            update["messages"] = [HumanMessage(content=result.standalone_query, id=orig.id)]
        return update
    except Exception as exc:
        # 意图识别本身失败不该误伤正常提问:默认放行,让后续流程照常跑。
        logger.warning(f"意图识别失败,默认放行:{exc}")
        writer(WSStepInfo(step="意图识别", status="success", data={"should_continue": True}))
        return {"should_continue": True}
