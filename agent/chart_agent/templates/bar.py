"""柱状图:1 分类轴 + 1 数值轴。

注意:cardinality > 15 时建议截断显示 Top 15,避免横轴太密(此处不强制,由决策层判断)。
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    x_field = decision.x_field
    y_field = decision.y_field

    if not x_field or not y_field:
        raise ValueError(f"bar 模板需要 x_field 和 y_field,当前: x={x_field}, y={y_field}")

    # 默认按 y 降序排(用户最常关心排行),除非数据自带顺序
    sorted_rows = sorted(
        rows,
        key=lambda r: (r.get(y_field) is None, -(r.get(y_field) or 0)),
    )

    x_data = [r.get(x_field) for r in sorted_rows]
    y_data = [r.get(y_field) for r in sorted_rows]

    return {
        "chart_type": "bar",
        "title": {"text": decision.title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": x_data,
            "name": x_field,
            "axisLabel": {"interval": 0, "rotate": 30 if len(x_data) > 6 else 0},
        },
        "yAxis": {
            "type": "value",
            "name": y_field,
        },
        "series": [
            {
                "name": y_field,
                "type": "bar",
                "data": y_data,
            }
        ],
    }
