"""ECharts 模板。每个模板是一个纯函数:render(decision, rows) -> dict。

设计原则:
1. 纯函数,无副作用
2. 输出的 dict 是合法的 ECharts option,前端拿到直接 setOption
3. 不依赖 pandas / numpy,纯 Python
4. 失败时抛异常,由 renderer.py 兜底降级到 table
"""
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

__all__ = [
    "bar",
    "empty",
    "error",
    "line",
    "metric",
    "multi_line",
    "pie",
    "stacked_bar",
    "table",
]
