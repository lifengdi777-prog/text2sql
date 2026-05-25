"""饼图:1 个分类列 + 1 个数值列,展示占比/构成。

ECharts pie 的 option 结构跟 bar/line 完全不同:
- 没有 xAxis / yAxis
- series[0].data 是 [{name, value}] 对象数组(不是简单数值)
- tooltip 用 item trigger 而不是 axis

决策层(decider.py)应该确保 cardinality ≤ 7 才走 pie,
超过 7 应该降级到 bar(否则饼图扇区太密)。这里不强制,以决策为准。
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    x_field = decision.x_field      # 分类列(如 factory_name)
    y_field = decision.y_field      # 数值列(如 actual_quantity)

    if not x_field or not y_field:
        raise ValueError(f"pie 模板需要 x_field 和 y_field,当前: x={x_field}, y={y_field}")

    # 按数值降序:大扇区在前,视觉上更友好(顺时针从大到小)
    sorted_rows = sorted(
        rows,
        key=lambda r: (r.get(y_field) is None, -(r.get(y_field) or 0)),
    )

    data = [
        {"name": str(r.get(x_field, "")), "value": r.get(y_field) or 0}
        for r in sorted_rows
    ]

    return {
        "chart_type": "pie",
        "title": {"text": decision.title, "left": "center"},
        # 悬浮显示:名称 + 数值 + 百分比
        "tooltip": {"trigger": "item", "formatter": "{b}<br/>{c} ({d}%)"},
        "legend": {
            "orient": "vertical",
            "left": "left",
            "top": "middle",
            "type": "scroll",  # 数据多时支持滚动
        },
        "series": [
            {
                "name": decision.title,
                "type": "pie",
                # 环形饼图,中心镂空更现代;实心饼图把 radius 改成单值即可
                "radius": ["38%", "68%"],
                "center": ["58%", "52%"],
                "avoidLabelOverlap": True,
                "itemStyle": {
                    "borderRadius": 4,
                    "borderColor": "#fff",
                    "borderWidth": 2,
                },
                "label": {
                    "show": True,
                    "formatter": "{b}\n{d}%",
                    "fontSize": 12,
                },
                "labelLine": {"show": True},
                "data": data,
            }
        ],
    }
