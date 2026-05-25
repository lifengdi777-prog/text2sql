"""Layer 3:渲染分发节点。

根据 chart_decision.chart_type 调对应的 template render 函数。
任何异常都降级为 table 渲染,保证前端永远拿到合法的 chart_config。
"""
from __future__ import annotations

from typing import Any, Callable

from langgraph.runtime import Runtime

from agent.chart_agent.schemas import ChartDecision
from agent.chart_agent.templates import (
    bar,
    empty,
    error,
    line,
    metric,
    multi_line,
    pie,
    stacked_bar,
    table,
)
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


# chart_type → render 函数。9 种全部真实实现,任意 LLM 决策都能渲染到对应图表。
_RENDERERS: dict[str, Callable[..., dict[str, Any]]] = {
    "line": line.render,
    "bar": bar.render,
    "table": table.render,
    "metric": metric.render,
    "empty": empty.render,
    "error": error.render,
    "multi_line": multi_line.render,    # 阶段 2:真多系列折线(数据透视)
    "stacked_bar": stacked_bar.render,  # 阶段 2:真堆叠柱状(数据透视)
    "pie": pie.render,                  # 阶段 2:真饼图(name/value 对象数组)
}


async def render_chart(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    decision = state.chart_decision
    rows = state.sql_result or []

    if decision is None:
        # 理论上不该发生:check_result_state 一定会写入一个 decision
        from agent.chart_agent.templates.error import render as render_error
        config = render_error(
            ChartDecision(chart_type="error", title="未知错误",
                          reason="chart_decision missing"),
            rows,
            error_message="未生成图表决策",
        )
    else:
        renderer = _RENDERERS.get(decision.chart_type, table.render)
        try:
            config = renderer(decision, rows)
        except Exception as exc:
            logger.exception(f"渲染 {decision.chart_type} 失败,fallback 到 table")
            config = table.render(decision, rows)
            config["_fallback_reason"] = str(exc)

    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}
