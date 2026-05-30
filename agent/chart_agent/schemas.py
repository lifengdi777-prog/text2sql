"""Chart Agent 内部数据结构。

- ChartTypeDecision:LLM 唯一产出(只选 chart_type)。
- DataShape / ColumnFeature:analyzer 对结果集做的形状摘要,供选型与构图用。

ECharts option 不再由 LLM 产出,改由 option_builder 用代码确定性构造。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


ChartType = Literal[
    "line", "bar", "pie", "multi_line", "stacked_bar", "table",
    "metric", "empty", "error",
]

SemanticType = Literal["temporal", "categorical", "numeric"]


class ChartTypeDecision(BaseModel):
    """LLM 的唯一职责:从兼容类型里挑一个最贴合用户意图的 chart_type。

    不再让 LLM 产出完整 ECharts option(透视/填数据交给 option_builder 用代码做),
    所以这个结构极小、生成快、几乎不会出错。
    """
    chart_type: ChartType
    reason: str = ""


class ColumnFeature(BaseModel):
    name: str
    dtype: str
    semantic_type: SemanticType
    cardinality: int
    sample: list[Any]
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    # 数值列各行求和:用于判断饼图是否成立(真·占比会≈100,逐组比率不会)
    sum_value: Optional[float] = None


class DataShape(BaseModel):
    row_count: int
    columns: list[ColumnFeature]
    shape_pattern: str
