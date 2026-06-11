"""归因综合节点:LLM 读现象 + 算好的贡献清单 → 只写核心结论,数字零计算。

输入是 run_dims 纯代码算好的贡献清单(成员变化量/增幅/贡献度),LLM 照着说即可
(参考 interpret_result 的"Python 算好统计量喂给 LLM"先例),消灭 LLM 算数。

流末发结构化 `attribution_result` 事件(finish=True),payload 即归因面板的渲染数据:
  {"phenomenon": {target_value, baseline_value, change, change_pct,
                  target_period, baseline_period, metric, ...},
   "dimensions": [{"name", "members": [{member, target_value, baseline_value,
                                        change, change_pct, contribution_pct}]}],
   "conclusion": "..."}
dimensions 按信息量排序(LLM 选的主维度排第一)。
LLM 综合失败时兜底:结论退化为"现象描述 + 贡献数据已给出",结构化事件照发,不断流。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.attribution_agent.schemas import AttributionContext, AttributionState
from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "synthesize.md"
_PROMPT_CACHE: str | None = None


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


class SynthesisResult(BaseModel):
    conclusion: str
    main_dimension: str = ""


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def _render_contributions(dim_results: list[dict]) -> str:
    """贡献清单 → 给 LLM 的紧凑行文本(数字已算好,LLM 只负责照着说)。"""
    blocks = []
    for d in dim_results:
        lines = [f"## 维度:{d['dimension']}"]
        for m in d["members"]:
            pct = f"{m['change_pct']:+.1f}%" if m["change_pct"] is not None else "基准期为 0"
            contrib = (f"贡献度 {m['contribution_pct']:.1f}%"
                       if m["contribution_pct"] is not None else "贡献度 -")
            lines.append(f"- {m['member']}:观察期 {_fmt(m['target_value'])},"
                         f"基准期 {_fmt(m['baseline_value'])},"
                         f"变化 {_fmt(m['change'])}({pct}),{contrib}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _llm_synthesize(phenomenon: dict, dim_results: list[dict]) -> SynthesisResult:
    """LLM 综合归因。独立成函数,便于测试替换。"""
    structured = llm.with_structured_output(SynthesisResult, method="json_mode")
    return await structured.ainvoke([  # type: ignore
        SystemMessage(content=_get_prompt()),
        HumanMessage(content=f"# 现象(已确认)\n{phenomenon.get('description')}\n\n"
                             f"# 各维度贡献清单(数字已由代码算好)\n"
                             f"{_render_contributions(dim_results)}"),
    ])


_PHENOMENON_KEYS = ("target_value", "baseline_value", "change", "change_pct",
                    "target_period", "baseline_period", "metric", "scope",
                    "compare_type", "description")


async def synthesize(state: AttributionState, runtime: Runtime[AttributionContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="综合归因", status="running"))
    phenomenon = state.phenomenon or {}
    dim_results = state.dim_results or []

    try:
        result = await _llm_synthesize(phenomenon, dim_results)
    except Exception as exc:  # noqa: BLE001
        # 兜底:现象与贡献度都是代码算的、完全可信,至少把它们给到用户
        logger.warning(f"归因综合失败,使用兜底结论:{exc}")
        result = SynthesisResult(
            conclusion=f"{phenomenon.get('description', '')}\n"
                       f"(综合分析暂不可用,下方已给出各维度的贡献度数据,可自行查看)",
            main_dimension="",
        )

    # 主维度(LLM 选的)排第一,面板默认展示它的贡献条形图
    dims = sorted(dim_results, key=lambda d: d["dimension"] != result.main_dimension)
    payload: dict[str, Any] = {
        "phenomenon": {k: phenomenon.get(k) for k in _PHENOMENON_KEYS},
        "dimensions": [{"name": d["dimension"], "members": d["members"],
                        "target_sql": d.get("target_sql"),
                        "baseline_sql": d.get("baseline_sql")} for d in dims],
        "conclusion": result.conclusion,
    }
    writer(WSStepInfo(step="综合归因", status="success"))
    writer(WSStepInfo(step="attribution_result", status="success",
                      data=payload, finish=True))
    logger.info(f"归因结论(main={result.main_dimension!r}):{result.conclusion[:120]}...")
    return {"conclusion": result.conclusion}
