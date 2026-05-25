"""错误卡:SQL 执行失败时的友好展示。

前端约定:红色 alert 卡片,展示 message + hint。
"""
from __future__ import annotations

from typing import Any

from agent.chart_agent.schemas import ChartDecision


def _make_friendly_hint(error_message: str) -> str:
    """根据错误信息给出友好建议。"""
    low = (error_message or "").lower()
    if "unknown column" in low or "unknown field" in low:
        return "可能是字段名拼写错误或表中没有该字段,请尝试换种说法"
    if "you have an error in your sql syntax" in low or "syntax" in low:
        return "SQL 语法错误,可能是问题表达不够清晰,请尝试更明确的提问"
    if "doesn't exist" in low or "no such table" in low:
        return "查询的表不存在,可能问题涉及到了系统未涵盖的数据"
    if "timeout" in low:
        return "查询超时,请尝试缩小时间范围或加更多筛选条件"
    return "请尝试调整提问表达,或换个角度提问"


def render(
    decision: ChartDecision,
    rows: list[dict[str, Any]],
    error_message: str = "",
    original_sql: str | None = None,
) -> dict[str, Any]:
    return {
        "chart_type": "error",
        "title": decision.title or "查询失败",
        "message": error_message or "未知错误",
        "hint": _make_friendly_hint(error_message),
        "original_sql": original_sql,   # 给开发者调试用,前端可以折叠展示
    }
