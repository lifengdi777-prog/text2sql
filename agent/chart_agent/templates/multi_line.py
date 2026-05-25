"""多系列折线图:1 时间/有序列 + 1 分类(系列) + 1 数值。

核心是数据透视——把"长表"变成 ECharts 要的"宽表"格式:

输入(长表,SQL 直接出的格式):
    month | line_name | actual_quantity
    1     | A线        | 100
    1     | B线        | 200
    2     | A线        | 110
    2     | B线        | 220

ECharts 要的(宽表,每个 series 一条独立的线):
    xAxis: [1, 2]
    series: [
      {name: 'A线', data: [100, 110]},
      {name: 'B线', data: [200, 220]},
    ]

ECharts 对缺失数据(某个 series 在某个 x 上没值)的处理:
- 用 None / null → 该点不连线(留空)
- 用 0  → 当成真实 0 处理(可能误导)
- 这里用 None,语义上"无数据"更准确。
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def _safe_sort(values: set[Any]) -> list[Any]:
    """对可能混合类型的值做安全排序——优先用原始类型,失败 fallback 到字符串。"""
    try:
        return sorted(values)
    except TypeError:
        # 混合类型(如 int + str),用 str 兜底
        return sorted(values, key=str)


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    x_field = decision.x_field            # 时间/有序列(如 month)
    series_field = decision.series_field  # 分类列(如 line_name)
    y_field = decision.y_field            # 数值列(如 actual_quantity)

    if not x_field or not y_field or not series_field:
        raise ValueError(
            f"multi_line 模板需要 x_field / series_field / y_field 全部存在,"
            f"当前: x={x_field}, series={series_field}, y={y_field}"
        )

    # 1. 抽出 unique x 值(按时间/有序列排序)
    x_values = _safe_sort({r[x_field] for r in rows if r.get(x_field) is not None})

    # 2. 抽出 unique series 名称(每个 series 对应一条独立的线)
    series_names = _safe_sort({str(r[series_field]) for r in rows
                               if r.get(series_field) is not None})

    # 3. 构造 (x, series) → y 查找表,用于透视
    lookup: dict[tuple[Any, str], Any] = {}
    for r in rows:
        x = r.get(x_field)
        s = r.get(series_field)
        if x is None or s is None:
            continue
        lookup[(x, str(s))] = r.get(y_field)

    # 4. 为每个 series 构造数据数组——按 x_values 顺序填充
    series_list = [
        {
            "name": s,
            "type": "line",
            "smooth": True,
            "showSymbol": True,
            "symbolSize": 6,
            "emphasis": {"focus": "series"},
            # 缺失数据用 None,ECharts 不连线
            "data": [lookup.get((x, s)) for x in x_values],
        }
        for s in series_names
    ]

    return {
        "chart_type": "multi_line",
        "title": {"text": decision.title, "left": "center"},
        "tooltip": {"trigger": "axis"},
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
            "boundaryGap": False,  # 折线图首尾贴轴更好看
        },
        "yAxis": {"type": "value"},
        "series": series_list,
    }
