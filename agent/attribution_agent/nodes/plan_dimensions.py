"""维度拆解规划节点:按领域元数据规划 2~4 个拆解维度,每维度一条"两期对比"子问题。"""
from __future__ import annotations

from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.attribution_agent.schemas import (AttributionContext, AttributionState,
                                             AttributionTarget, DimensionPlan)
from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "plan_dimensions.md"
_PROMPT_CACHE: str | None = None

# 拆解维度数上限(预算护栏:每个维度是一次完整子查询)
MAX_DIMENSIONS = 4


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def _render_target(t: AttributionTarget) -> str:
    return (f"归因指标:{t.metric}\n范围限定:{t.scope or '(无)'}\n"
            f"目标期:{t.target_period}\n基准期:{t.baseline_period}\n"
            f"现象方向:{t.direction}")


async def _llm_plan(target: AttributionTarget, domain_md: str) -> DimensionPlan:
    """LLM 规划拆解维度。独立成函数,便于测试替换。"""
    structured = llm.with_structured_output(DimensionPlan, method="json_mode")
    return await structured.ainvoke([  # type: ignore
        SystemMessage(content=_get_prompt()),
        SystemMessage(content=f"# 数据领域\n{domain_md or '(领域描述缺失,只规划最通用的维度)'}"),
        HumanMessage(content=_render_target(target)),
    ])


async def plan_dimensions(state: AttributionState, runtime: Runtime[AttributionContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="规划拆解维度", status="running"))
    assert state.target is not None  # 路由保证:parse_target 成功才会到这里

    try:
        plan = await _llm_plan(state.target, runtime.context.domain_md)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"维度规划失败:{exc}")
        writer(WSStepInfo(step="规划拆解维度", status="error",
                          data={"error": "维度规划失败,请稍后重试"}, finish=True))
        return {"should_continue": False, "error": str(exc)}

    dims = [d.model_dump() for d in plan.dimensions[:MAX_DIMENSIONS] if d.name and d.question]
    if not dims:
        writer(WSStepInfo(
            step="规划拆解维度", status="success",
            data={"clarify": "当前数据里没有找到可用于拆解归因的维度,无法进一步分析"},
            finish=True,
        ))
        return {"should_continue": False}

    writer(WSStepInfo(step="规划拆解维度", status="success",
                      data={"dimensions": [d["name"] for d in dims]}))
    logger.info(f"归因拆解维度:{[d['name'] for d in dims]}")
    return {"plan": dims}
