"""空结果卡:SQL 跑通但结果集为 []。"""
from __future__ import annotations

from typing import Any


def render(title: str = "查询无数据") -> dict[str, Any]:
    return {
        "chart_type": "empty",
        "title": title,
        "message": "在当前筛选条件下未找到任何数据",
        "hint": "请尝试放宽时间范围、去掉部分筛选条件,或换个角度提问",
    }
