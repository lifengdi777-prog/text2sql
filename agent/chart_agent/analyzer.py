"""数据形状分析:把 SQL 结果集 reduce 成 DataShape 喂给 LLM。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from agent.chart_agent.schemas import ColumnFeature, DataShape, SemanticType


_TEMPORAL_NAME_HINTS = (
    "date", "year", "month", "quarter", "day", "week", "time",
    "年", "月", "季", "周", "日期", "时间", "星期",
)
_ID_NAME_SUFFIXES = ("_id",)


def _infer_semantic_type(col_name: str, values: list[Any]) -> SemanticType:
    name_low = col_name.lower()
    if any(h in name_low for h in _TEMPORAL_NAME_HINTS):
        return "temporal"
    if any(name_low.endswith(s) for s in _ID_NAME_SUFFIXES):
        return "categorical"
    if not values:
        return "categorical"

    sample = values[0]
    if isinstance(sample, (datetime, date)):
        return "temporal"
    if isinstance(sample, bool):
        return "categorical"
    # Decimal 不是 int/float 的子类,SUM()/AVG() 结果常是 Decimal,必须显式纳入,
    # 否则指标列会被误判成 categorical,导致图表类型几乎只剩 table。
    if isinstance(sample, (int, float, Decimal)):
        return "numeric"
    return "categorical"


def _infer_pattern(cols: list[ColumnFeature]) -> str:
    n_temp = sum(c.semantic_type == "temporal" for c in cols)
    n_cat = sum(c.semantic_type == "categorical" for c in cols)
    n_num = sum(c.semantic_type == "numeric" for c in cols)

    if n_temp >= 1 and n_cat >= 1 and n_num >= 1:
        return "time_series_with_dim"
    if n_temp >= 1 and n_num >= 1:
        return "time_series"
    if n_cat >= 2 and n_num >= 1:
        return "cross_dim"
    if n_cat == 1 and n_num >= 2:
        return "cat_multi_metric"
    if n_cat == 1 and n_num == 1:
        return "cat_metric"
    if n_num == 1 and n_cat == 0 and n_temp == 0:
        return "single_value"
    if len(cols) >= 4:
        return "detail"
    return "unknown"


def _to_hashable(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return tuple(v)
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    return v


def analyze(rows: list[dict[str, Any]]) -> DataShape:
    if not rows:
        return DataShape(row_count=0, columns=[], shape_pattern="empty")

    col_names = list(rows[0].keys())
    features: list[ColumnFeature] = []

    for col in col_names:
        values = [r[col] for r in rows if r.get(col) is not None]
        st = _infer_semantic_type(col, values)
        unique_values = list({_to_hashable(v) for v in values})

        features.append(ColumnFeature(
            name=col,
            dtype=type(values[0]).__name__ if values else "NoneType",
            semantic_type=st,
            cardinality=len(unique_values),
            sample=unique_values[:5],
            min_value=float(min(values)) if st == "numeric" and values else None,
            max_value=float(max(values)) if st == "numeric" and values else None,
        ))

    return DataShape(
        row_count=len(rows),
        columns=features,
        shape_pattern=_infer_pattern(features),
    )


# 与 validator 保持一致的可读性上限
_PIE_MAX_CARD = 10
_BAR_MAX_CARD = 30
_SERIES_MAX_CARD = 8   # 多系列(multi_line/stacked_bar)的系列数上限


def compatible_chart_types(shape: DataShape | None) -> list[str]:
    """根据数据形状,确定性算出所有兼容的图表类型(无 LLM)。供前端切换菜单用。

    规则就是"这个类型的前提条件满不满足",不是打分:
    - line:  1 维度(时间) + 1 数值
    - bar:   1 维度 + 1 数值,维度基数 ≤ 30
    - pie:   1 维度 + 1 数值,维度基数 ≤ 10
    - multi_line / stacked_bar: 1 时间 + 1 低基数分类(系列) + 1 数值
    - stacked_bar: 2 分类 + 1 数值
    - table: 永远兜底
    """
    if shape is None or not shape.columns:
        return ["table"]

    temporal = [c for c in shape.columns if c.semantic_type == "temporal"]
    categorical = [c for c in shape.columns if c.semantic_type == "categorical"]
    numeric = [c for c in shape.columns if c.semantic_type == "numeric"]
    n_temp, n_cat, n_num = len(temporal), len(categorical), len(numeric)

    types: list[str] = []
    dim_cols = temporal + categorical

    # 1 维度 + ≥1 数值 → line/bar/pie 这组(多数值时用第一个做主指标,如带了占比列)
    if len(dim_cols) == 1 and n_num >= 1:
        dim = dim_cols[0]
        if dim.semantic_type == "temporal":
            types.append("line")
        if dim.cardinality <= _BAR_MAX_CARD:
            types.append("bar")
        if dim.cardinality <= _PIE_MAX_CARD:
            types.append("pie")

    # 多系列:需要透视
    if n_temp >= 1 and n_cat >= 1 and n_num >= 1:
        if categorical[0].cardinality <= _SERIES_MAX_CARD:
            types += ["multi_line", "stacked_bar"]
    elif n_cat >= 2 and n_num >= 1:
        if categorical[1].cardinality <= _SERIES_MAX_CARD:
            types.append("stacked_bar")

    if "table" not in types:
        types.append("table")
    return types


def chart_field_map(shape: DataShape | None) -> dict[str, str]:
    """给前端本地构图用的字段映射:dimension(X轴) / measure(数值) / series(分组,可选)。"""
    if shape is None:
        return {}
    temporal = [c.name for c in shape.columns if c.semantic_type == "temporal"]
    categorical = [c.name for c in shape.columns if c.semantic_type == "categorical"]
    numeric = [c.name for c in shape.columns if c.semantic_type == "numeric"]

    fm: dict[str, str] = {}
    if numeric:
        fm["measure"] = numeric[0]

    # 多系列:X 用时间(或第一个分类),series 用分组分类
    if temporal and categorical:
        fm["dimension"] = temporal[0]
        fm["series"] = categorical[0]
    elif len(categorical) >= 2:
        fm["dimension"] = categorical[0]
        fm["series"] = categorical[1]
    elif temporal:
        fm["dimension"] = temporal[0]
    elif categorical:
        fm["dimension"] = categorical[0]
    return fm
