"""Layer 2:Agent 决策节点。

只在"有正常数据"的分支才会被调用——
empty / error / metric 这 3 种状态在 subgraph 入口已经被 deterministic 分流掉了。

输出极简:chart_type + 字段映射,~50 token,不出 ECharts JSON。
"""
from __future__ import annotations

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.chart_agent.schemas import ChartDecision
from agent.llm import llm
from agent.prompts import load_prompt
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


async def decide_chart(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="图表决策", status="running"))

    try:
        # 用户原始问题(用于语义判断"对比/占比/趋势")
        query = state.messages[0].content if state.messages else ""
        data_shape = state.data_shape

        prompt = await load_prompt("chart_decider")
        structured_llm = llm.with_structured_output(ChartDecision, method="json_mode")

        decision: ChartDecision = await structured_llm.ainvoke([  # type: ignore
            SystemMessage(content=prompt),
            HumanMessage(content=(
                f"用户原始问题:{query}\n\n"
                f"数据形状(只看摘要,不看 raw 数据):\n"
                f"{data_shape.model_dump_json(indent=2) if data_shape else '{}'}\n\n"
                f"请从 6 种正常图表(line/multi_line/bar/stacked_bar/pie/table)里选一个,"
                f"并映射 x/y/series 字段。"
            )),
        ])

        logger.info(f"图表决策:type={decision.chart_type}, x={decision.x_field}, "
                    f"y={decision.y_field}, series={decision.series_field}")

        writer(WSStepInfo(
            step="图表决策",
            status="success",
            data=decision.model_dump(),
        ))
        return {"chart_decision": decision}

    except Exception as exc:
        # 决策失败 fallback:走 table,保证子图不挂
        logger.exception(f"图表决策失败,fallback 到 table:{exc}")
        cols = state.data_shape.columns if state.data_shape else []
        fallback = ChartDecision(
            chart_type="table",
            title="查询结果",
            x_field=cols[0].name if cols else None,
            y_field=cols[-1].name if len(cols) > 1 else None,
            reason=f"决策异常,fallback table:{exc}",
        )
        writer(WSStepInfo(step="图表决策", status="error",
                          data={"error": str(exc), "fallback": fallback.model_dump()}))
        return {"chart_decision": fallback}
