"""归因目标解析节点:指标/范围/观察期。

口径前置:对比口径由前端弹层选定后随请求传入(state.compare_type),
LLM 只识别指标/范围/观察期并给出两个候选基准期,基准期由代码按口径回填。
有显式口径后单期结果也可归因;只有连观察期都识别不出才 feasible=false。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

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


_BASIS_CN = {"mom": "环比(与上一可比期对比)", "yoy": "同比(与去年同期对比)"}


async def _llm_parse(question: str, history: list[dict] | None,
                     seed_question: str | None = None,
                     seed_rows: list[dict] | None = None,
                     compare_type: str = "mom",
                     target_period: str | None = None) -> AttributionTarget:
    """LLM 解析归因目标。结果模式(归因按钮)额外注入当前查询与结果数据。独立成函数,便于测试替换。"""
    msgs = [
        SystemMessage(content=_get_prompt()),
        SystemMessage(content=f"当前日期:{datetime.now():%Y-%m-%d}"),
        SystemMessage(content=f"用户已选口径:{_BASIS_CN.get(compare_type, compare_type)}"),
        SystemMessage(content="# 对话历史(供指代消解)\n" + _render_history(history)),
    ]
    if target_period:
        msgs.append(SystemMessage(content=(
            f"用户已选观察期:{target_period}"
            "(target_period 必须用它;mom_baseline / yoy_baseline 按它推导)"
        )))
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

    compare = state.compare_type or "mom"
    try:
        target = await _llm_parse(question, state.history,
                                  seed_question=state.seed_question, seed_rows=state.seed_rows,
                                  compare_type=compare, target_period=state.target_period)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"归因目标解析失败:{exc}")
        writer(WSStepInfo(step="解析归因目标", status="error",
                          data={"error": "归因目标解析失败,请换一种问法重试"}, finish=True))
        return {"halt": True, "error": str(exc)}

    # 观察期前置:多期结果时用户在弹层里选过,原样回填(不信 LLM 的改写),
    # 与口径同理 —— 选择权在用户;单期结果没传,观察期由 LLM 从问题/结果里识别
    if state.target_period:
        target.target_period = state.target_period
    # 口径前置:LLM 不输出口径/基准期,代码按前端选定的口径从候选里回填
    target.compare_type = compare
    target.baseline_period = target.mom_baseline if compare == "mom" else target.yoy_baseline
    if target.feasible and not (target.target_period and target.baseline_period):
        target.feasible = False
        target.infeasible_reason = (target.infeasible_reason
                                    or "无法识别观察期或推导对比基准期")

    # 连观察期都识别不出(结果无时间信息、问题也没给期间)→ 说明,结束
    if not target.feasible:
        writer(WSStepInfo(
            step="解析归因目标", status="success",
            data={"clarify": target.infeasible_reason or "当前结果没有可归因的期间信息"},
            finish=True,
        ))
        logger.info(f"归因终止:结果不可归因({target.infeasible_reason!r})")
        return {"target": target, "halt": True}

    writer(WSStepInfo(
        step="解析归因目标", status="success",
        data={"metric": target.metric, "target_period": target.target_period,
              "baseline_period": target.baseline_period, "compare_type": target.compare_type},
    ))
    logger.info(f"归因目标:{target.metric} {target.scope} {target.target_period} "
                f"vs {target.baseline_period}({target.compare_type})")
    return {"target": target}
