"""Chart Agent 内部数据结构。

EChartsSpec 是 LLM 直接生成的 ECharts option,前端拿到就能 setOption。
顶层只约束最常用字段,其余 ECharts 配置(dataZoom / visualMap / animation ...)
通过 extra='allow' 放行,不在 schema 里逐一列。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ChartType = Literal[
    "line", "bar", "pie", "multi_line", "stacked_bar", "table",
    "metric", "empty", "error",
]

SemanticType = Literal["temporal", "categorical", "numeric"]


class ColumnFeature(BaseModel):
    name: str
    dtype: str
    semantic_type: SemanticType
    cardinality: int
    sample: list[Any]
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class DataShape(BaseModel):
    row_count: int
    columns: list[ColumnFeature]
    shape_pattern: str


class EChartsSpec(BaseModel):
    """LLM 直接产出的 ECharts option。

    设计要点:
    - title/series 必填,其他可空(table 类型就用不到 xAxis/yAxis/series)
    - extra='allow' 让 LLM 可以加 ECharts 任意字段(legend/grid/tooltip/...)
    - model_dump(exclude_none=True) 输出即合法 ECharts option
    """
    chart_type: ChartType
    title: dict[str, Any] | str
    tooltip: dict[str, Any] | None = None
    legend: dict[str, Any] | None = None
    grid: dict[str, Any] | None = None
    xAxis: dict[str, Any] | None = None
    yAxis: dict[str, Any] | None = None
    series: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""

    model_config = ConfigDict(extra="allow")

    def to_echarts_option(self) -> dict[str, Any]:
        d = self.model_dump(exclude_none=True)
        # reason 是给后端日志用的,前端不需要
        d.pop("reason", None)
        return d
