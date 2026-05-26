"""
根据 validator 报的 issues 让 LLM 修正 spec。
"""
from __future__ import annotations

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.chart_agent.schemas import EChartsSpec
from agent.llm import llm
from agent.prompts import load_prompt
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


_SAMPLE_ROWS = 30
MAX_RETRY = 2   # 最多重试 2 次,避免死循环


def _build_correct_message(
    query: str,
    last_spec_json: str,
    issues: list[str],
    rows: list[dict],
) -> str:
    sample = rows[:_SAMPLE_ROWS]
    issues_text = "\n".join(f"- {x}" for x in issues)
    return (
        f"用户原始问题:{query}\n\n"
        f"上一轮你产出的 spec:\n{last_spec_json}\n\n"
        f"校验报告(请逐条修复):\n{issues_text}\n\n"
        f"参考数据(前 {len(sample)} 行):\n{sample}\n\n"
        f"请输出修正后的完整 ECharts option JSON。"
    )


async def correct_spec(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    retry_count = state.chart_retry_count + 1
    writer(WSStepInfo(step=f"修正图表配置(第 {retry_count} 次)", status="running"))

    query = state.messages[0].content if state.messages else ""
    rows = state.sql_result or []
    issues = state.chart_issues or []
    last_spec_json = (
        state.chart_spec.model_dump_json(indent=2) if state.chart_spec else "{}"
    )

    prompt = await load_prompt("chart_spec_corrector")
    structured_llm = llm.with_structured_output(EChartsSpec, method="json_mode")

    try:
        new_spec: EChartsSpec = await structured_llm.ainvoke([  # type: ignore
            SystemMessage(content=prompt),
            HumanMessage(content=_build_correct_message(
                str(query), last_spec_json, issues, rows,
            )),
        ])
        logger.info(
            f"图表 spec 第 {retry_count} 次修正:type={new_spec.chart_type}, "
            f"reason={new_spec.reason}"
        )
        writer(WSStepInfo(
            step=f"修正图表配置(第 {retry_count} 次)",
            status="success",
            data={"chart_type": new_spec.chart_type, "reason": new_spec.reason},
        ))
        return {"chart_spec": new_spec, "chart_retry_count": retry_count}

    except Exception as exc:
        logger.exception(f"图表 spec 修正异常:{exc}")
        writer(WSStepInfo(
            step=f"修正图表配置(第 {retry_count} 次)",
            status="error",
            data={"error": str(exc)},
        ))
        return {
            "chart_spec": None,
            "chart_error": str(exc),
            "chart_retry_count": retry_count,
        }
