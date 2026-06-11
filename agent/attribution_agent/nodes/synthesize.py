"""归因综合节点:LLM 读现象 + 各维度小表 → 主要贡献项 + 归因结论。

向前端发三件套(全部走现有协议,前端零改动):
  1. 主维度两期对比表  —— 数组 + finish 事件(附该维度 SQL,可查看/导出/切表格);
  2. 对比图表          —— 静默调 chart_subgraph 取配置,以 finish 事件发出;
  3. 归因结论          —— "数据解读"事件(前端解读区直接渲染,落库可回放)。
LLM 综合失败时兜底:结论退化为"现象描述 + 各维度数据已给出",三件套照发,不断流。
"""
from __future__ import annotations

import json
from pathlib import Path

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


async def _llm_synthesize(phenomenon: dict, dim_results: list[dict]) -> SynthesisResult:
    """LLM 综合归因。独立成函数,便于测试替换。"""
    dims_md = "\n\n".join(
        f"## 维度:{d['dimension']}\n" + json.dumps(d["rows"], ensure_ascii=False, default=str)
        for d in dim_results
    )
    structured = llm.with_structured_output(SynthesisResult, method="json_mode")
    return await structured.ainvoke([  # type: ignore
        SystemMessage(content=_get_prompt()),
        HumanMessage(content=f"# 现象(已确认)\n{phenomenon.get('description')}\n\n"
                             f"# 各维度两期对比数据\n{dims_md}"),
    ])


async def _build_chart_config(question: str, rows: list[dict]) -> dict | None:
    """静默调 chart_agent 给主维度出对比图;失败/不可成图返回 None(表格已兜底)。"""
    from agent.attribution_agent.adapters import _invoke_silently
    from agent.chart_agent import chart_subgraph
    from agent.chart_agent.schemas import ChartAgentState

    try:
        state = ChartAgentState(messages=[HumanMessage(content=question)],
                                sql_result=rows, source_question=question)
        final = await _invoke_silently(chart_subgraph, state, None)
        config = final.get("chart_config")
        return config if isinstance(config, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"归因主图生成失败(仅展示表格):{exc}")
        return None


async def synthesize(state: AttributionState, runtime: Runtime[AttributionContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="综合归因", status="running"))
    phenomenon = state.phenomenon or {}
    dim_results = state.dim_results or []
    t = state.target

    try:
        result = await _llm_synthesize(phenomenon, dim_results)
    except Exception as exc:  # noqa: BLE001
        # 兜底:现象数字是代码算的、完全可信,至少把它和支撑数据给到用户
        logger.warning(f"归因综合失败,使用兜底结论:{exc}")
        result = SynthesisResult(
            conclusion=f"{phenomenon.get('description', '')}\n"
                       f"(综合分析暂不可用,下方已给出各维度的两期对比数据,可自行查看)",
            main_dimension="",
        )

    # 主维度:LLM 选的;选不出/没匹配上 → 第一个
    main = next((d for d in dim_results if d["dimension"] == result.main_dimension),
                dim_results[0] if dim_results else None)

    if main is not None:
        # 1) 支撑数据表(数组+finish,前端可查看 SQL/导出/切表格;落库后历史可回放)
        writer(WSStepInfo(step=f"维度对比数据({main['dimension']})", status="success",
                          data=main["rows"], sql=main.get("sql"), finish=True))
        # 2) 对比图表(两期×维度成员,decider 通常给 multi_line/stacked_bar)
        chart_q = (f"{t.baseline_period}和{t.target_period}{t.scope or ''}"
                   f"各{main['dimension']}的{t.metric}对比") if t else main["question"]
        config = await _build_chart_config(chart_q, main["rows"])
        if config is not None:
            writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))

    # 3) 归因结论(走"数据解读"事件,前端解读区渲染 + 落库)
    writer(WSStepInfo(step="数据解读", status="running", data=result.conclusion))
    writer(WSStepInfo(step="数据解读", status="success", data=result.conclusion))
    writer(WSStepInfo(step="综合归因", status="success"))
    logger.info(f"归因结论(main={result.main_dimension!r}):{result.conclusion[:120]}...")
    return {"conclusion": result.conclusion}
