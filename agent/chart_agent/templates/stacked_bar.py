"""堆叠柱状图:1 分类/时间 + 1 分类(系列) + 1 数值。

跟 multi_line 几乎一样,差别在:
1. series.type = 'bar' 而不是 'line'
2. series.stack = 'total':让所有 series 在同一根柱子上堆叠
3. 缺失数据用 0(堆叠场景下 None 会让某段柱子缺失,视觉不连续)

典型 query:
- "对比各产品类别 Q1 和 Q2 的实际产量" → x=类别, series=季度, y=产量
- "各工厂各设备类型的停机时长" → x=工厂, series=设备类型, y=停机时长
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def _safe_sort(values: set[Any]) -> list[Any]:
    try:
        return sorted(values)
    except TypeError:
        return sorted(values, key=str)


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    x_field = decision.x_field            # 主分类/时间
    series_field = decision.series_field  # 堆叠系列
    y_field = decision.y_field            # 数值

    if not x_field or not y_field or not series_field:
        raise ValueError(
            f"stacked_bar 模板需要 x_field / series_field / y_field 全部存在,"
            f"当前: x={x_field}, series={series_field}, y={y_field}"
        )

    x_values = _safe_sort({r[x_field] for r in rows if r.get(x_field) is not None})
    series_names = _safe_sort({str(r[series_field]) for r in rows
                               if r.get(series_field) is not None})

    lookup: dict[tuple[Any, str], Any] = {}
    for r in rows:
        x = r.get(x_field)
        s = r.get(series_field)
        if x is None or s is None:
            continue
        lookup[(x, str(s))] = r.get(y_field)

    series_list = [
        {
            "name": s,
            "type": "bar",
            "stack": "total",                         # ← 关键:同名 stack 让所有 series 叠起来
            "emphasis": {"focus": "series"},
            "label": {"show": False},                 # 段太多时显示 label 会糊,默认关
            # 堆叠场景下缺失值用 0(语义:这个 series 在该 x 上贡献 0)
            "data": [lookup.get((x, s), 0) or 0 for x in x_values],
        }
        for s in series_names
    ]

    return {
        "chart_type": "stacked_bar",
        "title": {"text": decision.title, "left": "center"},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {
            "data": list(series_names),
            "top": "bottom",
            "type": "scroll",
        },
        "grid": {"left": "3%", "right": "4%", "bottom": "15%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": list(x_values),
            "name": x_field,
            "axisLabel": {"interval": 0, "rotate": 30 if len(x_values) > 6 else 0},
        },
        "yAxis": {"type": "value"},
        "series": series_list,
    }
