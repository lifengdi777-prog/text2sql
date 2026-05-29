"""Chart Agent 内部数据结构。

EChartsSpec 是 LLM 直接生成的 ECharts option,前端拿到就能 setOption。
顶层只约束最常用字段,其余 ECharts 配置(dataZoom / visualMap / animation ...)
通过 extra='allow' 放行,不在 schema 里逐一列。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


ChartType = Literal[
    "line", "bar", "pie", "multi_line", "stacked_bar", "table",
    "metric", "empty", "error",
]

SemanticType = Literal["temporal", "categorical", "numeric"]

# chart_type → ECharts series.type 的确定性映射。
# ECharts 靠 series[].type 决定画什么图,而它与 chart_type 一一对应,
# 不该依赖 LLM 写对 —— 缺了就按这张表补全。
_CHART_TYPE_TO_SERIES_TYPE = {
    "line": "line",
    "multi_line": "line",
    "bar": "bar",
    "stacked_bar": "bar",
    "pie": "pie",
}


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

    @model_validator(mode="before")
    @classmethod
    def _hoist_nested_option(cls, data: Any) -> Any:
        """LLM 常把 ECharts 字段错套进 `option`/`spec`/`config` 包裹层,
        校验前提升到顶层,避免顶层 title/series 缺失而触发整轮 correct 重试。
        顶层已有的键优先,不被嵌套层覆盖。"""
        if not isinstance(data, dict):
            return data
        for wrapper in ("option", "spec", "config"):
            nested = data.get(wrapper)
            if isinstance(nested, dict):
                for k, v in nested.items():
                    data.setdefault(k, v)
                data.pop(wrapper, None)
        return data

    def to_echarts_option(self) -> dict[str, Any]:
        d = self.model_dump(exclude_none=True)
        # reason 是给后端日志用的,前端不需要
        d.pop("reason", None)
        # 确定性补 series.type:LLM 漏写时按 chart_type 补,避免前端渲染空白
        series_type = _CHART_TYPE_TO_SERIES_TYPE.get(self.chart_type)
        if series_type and isinstance(d.get("series"), list):
            for s in d["series"]:
                if isinstance(s, dict) and not s.get("type"):
                    s["type"] = series_type
        return d
