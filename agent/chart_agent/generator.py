"""LLM 直接产出 ECharts spec 的节点。
把数据形状 + 样本数据 + 用户问题一次性给 LLM,
直接输出完整 ECharts option。
"""
from __future__ import annotations

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.chart_agent.schemas import EChartsSpec
from agent.llm import llm
from agent.prompts import load_prompt
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


# 喂给 LLM 的样本行数。够它推断字段值,又不爆 token。
_SAMPLE_ROWS = 30


def _build_user_message(query: str, data_shape_json: str, rows: list[dict]) -> str:
    sample = rows[:_SAMPLE_ROWS]
    truncated_note = f"\n(共 {len(rows)} 行,只展示前 {_SAMPLE_ROWS} 行)" if len(rows) > _SAMPLE_ROWS else ""
    return (
        f"用户原始问题:{query}\n\n"
        f"数据形状摘要:\n{data_shape_json}\n\n"
        f"原始数据(用于填充 series.data):{truncated_note}\n"
        f"{sample}\n\n"
        f"请直接输出 ECharts option JSON。"
    )


async def generate_spec(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表配置", status="running"))

    query = state.messages[0].content if state.messages else ""
    rows = state.sql_result or []
    shape_json = state.data_shape.model_dump_json(indent=2) if state.data_shape else "{}"

    prompt = await load_prompt("chart_spec_generator")
    structured_llm = llm.with_structured_output(EChartsSpec, method="json_mode")

    try:
        spec: EChartsSpec = await structured_llm.ainvoke([  # type: ignore
            SystemMessage(content=prompt),
            HumanMessage(content=_build_user_message(str(query), shape_json, rows)),
        ])
        logger.info(f"图表 spec 生成:type={spec.chart_type}, reason={spec.reason}")
        writer(WSStepInfo(
            step="生成图表配置",
            status="success",
            data={"chart_type": spec.chart_type, "reason": spec.reason},
        ))
        return {"chart_spec": spec}

    except Exception as exc:
        logger.exception(f"图表 spec 生成异常:{exc}")
        # 兜底:让后续 validate 节点把它当失败处理,走 correct 重试
        writer(WSStepInfo(step="生成图表配置", status="error",
                          data={"error": str(exc)}))
        return {"chart_spec": None, "chart_error": str(exc)}
