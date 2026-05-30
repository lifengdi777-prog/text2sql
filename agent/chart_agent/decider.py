"""图表类型决策:LLM 从「兼容类型」里挑一个最贴合用户意图的 chart_type。

这是 chart_agent 里唯一的 LLM 调用,且只输出一个枚举 + 一句理由,
所以又快又稳。透视、填 series.data 等确定性工作交给 option_builder。
"""
from __future__ import annotations

from langchain.messages import HumanMessage, SystemMessage

from agent.chart_agent.schemas import ChartTypeDecision, DataShape
from agent.llm import llm
from agent.prompts import load_prompt
from core.log import logger


async def decide_chart_type(query: str, shape: DataShape, allowed: list[str]) -> tuple[str, str]:
    """返回 (chart_type, reason)。chart_type 一定落在 allowed 内(越界则回退 allowed[0])。"""
    prompt = await load_prompt("chart_type_picker")
    structured_llm = llm.with_structured_output(ChartTypeDecision, method="json_mode")

    user_msg = (
        f"用户问题:{query}\n\n"
        f"数据形状摘要:\n{shape.model_dump_json(indent=2)}\n\n"
        f"可选图表类型(只能从中选恰好一个):{allowed}\n\n"
        f"请输出 chart_type 与 reason。"
    )
    try:
        decision: ChartTypeDecision = await structured_llm.ainvoke([  # type: ignore
            SystemMessage(content=prompt),
            HumanMessage(content=user_msg),
        ])
        ct = decision.chart_type
        if ct not in allowed:
            logger.warning(f"LLM 选了不在兼容集里的类型 {ct},回退 {allowed[0]}")
            return allowed[0], f"LLM 选型 {ct} 越界,回退 {allowed[0]}"
        return ct, decision.reason
    except Exception as exc:
        # 选型失败不致命:回退到兼容集第一个(确定性算出的,一定能画)
        logger.exception(f"图表选型异常,回退 {allowed[0]}:{exc}")
        return allowed[0], f"选型异常回退:{exc}"
