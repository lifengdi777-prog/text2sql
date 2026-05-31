"""图表决策:LLM 选 chart_type + 指出字段映射(哪列当横轴/分组/数值)。

这是 chart_agent 里唯一的 LLM 调用,且只输出"选型 + 映射"这点判断,
不碰数据本身(透视、填 series.data 由 option_builder 用代码做),所以又快又稳。
"""
from __future__ import annotations

from langchain.messages import HumanMessage, SystemMessage

from agent.chart_agent.schemas import ChartTypeDecision, DataShape
from agent.llm import llm
from agent.prompts import load_prompt
from core.log import logger


async def decide_chart_type(query: str, shape: DataShape, allowed: list[str]) -> ChartTypeDecision | None:
    """让 LLM 从 allowed 里选类型并给出字段映射。

    返回 ChartTypeDecision(含 chart_type + x/value/series/value_fields 映射);
    调用失败返回 None,由上层用规则兜底。chart_type 是否越界、映射列名是否有效,均由上层校验。
    """
    prompt = await load_prompt("chart_type_picker")
    structured_llm = llm.with_structured_output(ChartTypeDecision, method="json_mode")

    user_msg = (
        f"用户问题:{query}\n\n"
        f"数据形状摘要(字段映射只能从下面的列名里选):\n{shape.model_dump_json(indent=2)}\n\n"
        f"可选图表类型(chart_type 只能从中选恰好一个):{allowed}\n\n"
        f"请输出 chart_type、字段映射(x_field / value_field / series_field / value_fields)与 reason。"
    )
    try:
        decision: ChartTypeDecision = await structured_llm.ainvoke([  # type: ignore
            SystemMessage(content=prompt),
            HumanMessage(content=user_msg),
        ])
        return decision
    except Exception as exc:
        logger.exception(f"图表选型异常,将由上层用规则兜底:{exc}")
        return None
