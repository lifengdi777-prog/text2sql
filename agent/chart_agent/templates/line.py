"""折线图:1 时间(或顺序)轴 + 1 数值轴。

ECharts option 结构:
  - xAxis.type = 'category'
  - series[0].type = 'line', smooth=True
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    x_field = decision.x_field
    y_field = decision.y_field

    if not x_field or not y_field:
        raise ValueError(f"line 模板需要 x_field 和 y_field,当前: x={x_field}, y={y_field}")

    # 按 x_field 排序,时间序列要单调
    sorted_rows = sorted(rows, key=lambda r: (r.get(x_field) is None, r.get(x_field)))

    x_data = [r.get(x_field) for r in sorted_rows]
    y_data = [r.get(y_field) for r in sorted_rows]

    return {
        "chart_type": "line",
        "title": {"text": decision.title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": x_data,
            "name": x_field,
        },
        "yAxis": {
            "type": "value",
            "name": y_field,
        },
        "series": [
            {
                "name": y_field,
                "type": "line",
                "smooth": True,
                "data": y_data,
            }
        ],
    }
