"""空结果卡:SQL 跑通但结果集为 []。

常见原因:筛选条件过严、时间范围内无数据。
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def render(decision: ChartDecision, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "chart_type": "empty",
        "title": decision.title or "查询无数据",
        "message": "在当前筛选条件下未找到任何数据",
        "hint": "请尝试放宽时间范围、去掉部分筛选条件,或换个角度提问",
    }
