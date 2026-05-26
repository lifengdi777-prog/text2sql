"""数据形状分析:把 SQL 结果集 reduce 成 DataShape 喂给 LLM。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agent.chart_agent.schemas import ColumnFeature, DataShape, SemanticType


_TEMPORAL_NAME_HINTS = ("date", "year", "month", "quarter", "day", "week", "time")
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
    if isinstance(sample, (int, float)):
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
