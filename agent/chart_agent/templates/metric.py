"""指标卡(KPI card):单值结果的展示。

触发条件:
- 1 行 1 列 numeric:`SELECT SUM(x) FROM ...`
- 1 行多列 numeric:`SELECT SUM(x), AVG(y) FROM ...`(多个指标并列展示)

前端约定:
- chart_type = "metric"
- metrics 是 [{label, value, unit?}] 数组
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


# 常见单位 hint:列名包含这些子串时,推断单位
_UNIT_HINTS = {
    "rate": "%",       # qualified_rate / defect_rate
    "ratio": "%",
    "percent": "%",
    "minutes": "分钟",
    "hours": "小时",
    "quantity": "件",
    "amount": "元",
}


def _infer_unit(col_name: str) -> str:
    low = col_name.lower()
    for hint, unit in _UNIT_HINTS.items():
        if hint in low:
            return unit
    return ""


def _format_value(v: Any, unit: str) -> Any:
    """如果是 rate/ratio 单位,自动乘 100 并保留 2 位小数。"""
    if v is None:
        return None
    if unit == "%" and isinstance(v, (int, float)):
        return round(float(v) * 100, 2)
    if isinstance(v, float):
        return round(v, 4)
    return v


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        # 理论上不会到这里(empty 分支已处理),保险起见
        return {
            "chart_type": "metric",
            "title": decision.title,
            "metrics": [],
        }

    row = rows[0]
    metrics = []
    for col_name, value in row.items():
        unit = _infer_unit(col_name)
        metrics.append({
            "label": col_name,
            "value": _format_value(value, unit),
            "unit": unit,
        })

    return {
        "chart_type": "metric",
        "title": decision.title,
        "metrics": metrics,
    }
