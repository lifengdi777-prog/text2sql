"""归因目标解析节点:指标/范围/目标期/对比口径。

口径规则(用户可自选):
  - 话术里明说了 同比/环比/具体基准 → 尊重;
  - 没说 → 不替用户猜:发澄清卡,给出"环比 vs 同比"两个可点选项,本轮结束。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.attribution_agent.schemas import AttributionContext, AttributionState, AttributionTarget
from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "parse_target.md"
_PROMPT_CACHE: str | None = None


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def _render_history(history: list[dict] | None) -> str:
    if not history:
        return "(无历史对话)"
    blocks = []
    for i, t in enumerate(history, 1):
        seg = [f"第 {i} 轮问题:{t.get('question', '')}"]
        rows = t.get("rows") or []
        if rows:
            seg.append(f"结果快照:{json.dumps(rows[:5], ensure_ascii=False, default=str)}")
        blocks.append("\n".join(seg))
    return "\n\n".join(blocks)


async def _llm_parse(question: str, history: list[dict] | None,
                     seed_question: str | None = None,
                     seed_rows: list[dict] | None = None) -> AttributionTarget:
    """LLM 解析归因目标。结果模式(归因按钮)额外注入当前查询与结果数据。独立成函数,便于测试替换。"""
    msgs = [
        SystemMessage(content=_get_prompt()),
        SystemMessage(content=f"当前日期:{datetime.now():%Y-%m-%d}"),
        SystemMessage(content="# 对话历史(供指代消解)\n" + _render_history(history)),
    ]
    if seed_rows:
        msgs.append(SystemMessage(content=(
            "# 结果模式\n当前查询:" + (seed_question or "") + "\n结果数据:"
            + json.dumps(seed_rows[:40], ensure_ascii=False, default=str)
        )))
    msgs.append(HumanMessage(content=question))
    structured = llm.with_structured_output(AttributionTarget, method="json_mode")
    return await structured.ainvoke(msgs)  # type: ignore


async def parse_target(state: AttributionState, runtime: Runtime[AttributionContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="解析归因目标", status="running"))
    question = str(state.messages[-1].content) if state.messages else ""

    try:
        target = await _llm_parse(question, state.history,
                                  seed_question=state.seed_question, seed_rows=state.seed_rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"归因目标解析失败:{exc}")
        writer(WSStepInfo(step="解析归因目标", status="error",
                          data={"error": "归因目标解析失败,请换一种问法重试"}, finish=True))
        return {"halt": True, "error": str(exc)}

    # 结果模式:当前结果没有可归因的变化(单期汇总等)→ 说明 + 引导,结束
    if not target.feasible:
        writer(WSStepInfo(
            step="解析归因目标", status="success",
            data={"clarify": target.infeasible_reason or "当前结果没有可归因的变化"},
            guide_queries=["各月份的实际产量"],
            finish=True,
        ))
        logger.info(f"归因终止:结果不可归因({target.infeasible_reason!r})")
        return {"target": target, "halt": True}

    # 口径没说 → 澄清:给出环比/同比两个可点选项,让用户自己选,绝不替用户猜
    if target.compare_type == "unspecified":
        guides = []
        if target.mom_baseline:
            guides.append(f"{question}(环比,对比{target.mom_baseline})")
        if target.yoy_baseline:
            guides.append(f"{question}(同比,对比{target.yoy_baseline})")
        writer(WSStepInfo(
            step="解析归因目标", status="success",
            data={"clarify": "需要先确定对比口径(环比还是同比),请点击选择"},
            guide_queries=guides or [f"{question}(环比)", f"{question}(同比)"],
            finish=True,
        ))
        logger.info(f"归因口径未指定,发澄清卡:{question!r}")
        return {"target": target, "halt": True}

    writer(WSStepInfo(
        step="解析归因目标", status="success",
        data={"metric": target.metric, "target_period": target.target_period,
              "baseline_period": target.baseline_period, "compare_type": target.compare_type},
    ))
    logger.info(f"归因目标:{target.metric} {target.scope} {target.target_period} "
                f"vs {target.baseline_period}({target.compare_type})")
    return {"target": target}
