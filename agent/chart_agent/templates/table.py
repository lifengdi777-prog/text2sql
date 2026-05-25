"""表格:行/列均按原顺序输出。

约定:前端按 columns + rows 渲染,列顺序就是 SELECT 顺序。
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "chart_type": "table",
            "title": decision.title,
            "columns": [],
            "rows": [],
        }

    columns = list(rows[0].keys())

    return {
        "chart_type": "table",
        "title": decision.title,
        "columns": columns,
        "rows": [[r.get(c) for c in columns] for r in rows],
        "row_count": len(rows),
    }
