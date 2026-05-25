"""Chart Agent 内部数据结构。

注意:这些 schema 嵌入 WSAgentState,会被序列化到 SSE 流里,前端拿到的就是这些字段。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


# 9 种 chart_type:6 种正常图表 + 3 种状态卡(metric/empty/error)
ChartType = Literal[
    "line",
    "multi_line",
    "bar",
    "stacked_bar",
    "pie",
    "table",
    "metric",   # 单值指标卡
    "empty",    # 空结果状态
    "error",    # SQL 报错状态
]

SemanticType = Literal["temporal", "categorical", "numeric"]


class ColumnFeature(BaseModel):
    """单列的特征摘要。喂给 LLM 决策时用,比 raw 数据省 token 且更准确。"""
    name: str
    dtype: str                                # 原始 Python 类型名:int/float/str/...
    semantic_type: SemanticType               # 语义类型(算出来的)
    cardinality: int                          # 不同值的数量
    sample: list[Any]                         # 前 5 个不同值
    min_value: Optional[float] = None         # 仅 numeric
    max_value: Optional[float] = None


class DataShape(BaseModel):
    """整个结果集的形状摘要。"""
    row_count: int
    columns: list[ColumnFeature]
    shape_pattern: str                        # time_series / cat_metric / cross_dim / ...


class ChartDecision(BaseModel):
    """Agent 决策输出。极简,只有 ~50 token。"""
    chart_type: ChartType
    title: str                                # 图表标题(中文,Agent 据 query 起)
    x_field: Optional[str] = None             # X 轴字段
    y_field: Optional[str] = None             # Y 轴字段(单指标时)
    series_field: Optional[str] = None        # 系列字段(stacked_bar / multi_line)
    reason: str = ""                          # 决策理由,可解释性 + 调试用
