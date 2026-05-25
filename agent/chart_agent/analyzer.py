"""Layer 1:数据形状分析(纯 Python,不用 LLM)。

为什么不用 LLM:
- 统计特征(列类型、cardinality、min/max)是确定性的,LLM 看不准还浪费 token
- 100 行数据 raw 喂给 LLM 是几百 token,算成 DataShape 后只有 ~50 token

为什么不用 pandas:
- 项目无 pandas 依赖,纯 Python 完全够用
- DW 表都是规整的结果集,不需要 pandas 那种重武器
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agent.chart_agent.schemas import ColumnFeature, DataShape, SemanticType


# 列名包含这些 token 时,优先判为 temporal(即使 dtype 是 int,如 date_id 字段)
_TEMPORAL_NAME_HINTS = ("date", "year", "month", "quarter", "day", "week", "time")

# 列名 endswith 这些时,即使是 int 也不算 numeric(避免把主键当指标)
_ID_NAME_SUFFIXES = ("_id",)


def _infer_semantic_type(col_name: str, values: list[Any]) -> SemanticType:
    """根据列名 hint + 值类型,推断语义类型。

    判断顺序:
    1. 列名含时间关键词 → temporal
    2. 列名以 _id 结尾 → categorical(避免 product_id 当指标)
    3. 第一个非空值是 datetime/date → temporal
    4. 第一个非空值是 int/float → numeric
    5. 其他 → categorical
    """
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
    """根据列组成推断 shape_pattern。

    pattern 是给 LLM 的"形状速记",帮助它快速选模板。
    """
    n_temp = sum(c.semantic_type == "temporal" for c in cols)
    n_cat = sum(c.semantic_type == "categorical" for c in cols)
    n_num = sum(c.semantic_type == "numeric" for c in cols)

    if n_temp >= 1 and n_cat >= 1 and n_num >= 1:
        return "time_series_with_dim"      # → multi_line / stacked_bar
    if n_temp >= 1 and n_num >= 1:
        return "time_series"                # → line
    if n_cat >= 2 and n_num >= 1:
        return "cross_dim"                  # → table / stacked_bar
    if n_cat == 1 and n_num >= 2:
        return "cat_multi_metric"           # → multi_line / table
    if n_cat == 1 and n_num == 1:
        return "cat_metric"                 # → bar / pie
    if n_num == 1 and n_cat == 0 and n_temp == 0:
        return "single_value"               # → metric(deterministic 分流时处理)
    if len(cols) >= 4:
        return "detail"                     # → table
    return "unknown"


def analyze(rows: list[dict[str, Any]]) -> DataShape:
    """主入口。把 SQL 结果集分析成 DataShape。"""
    if not rows:
        return DataShape(row_count=0, columns=[], shape_pattern="empty")

    col_names = list(rows[0].keys())
    features: list[ColumnFeature] = []

    for col in col_names:
        # 抽出该列的非空值
        values = [r[col] for r in rows if r.get(col) is not None]
        st = _infer_semantic_type(col, values)

        unique_values = list({_to_hashable(v) for v in values})

        feat = ColumnFeature(
            name=col,
            dtype=type(values[0]).__name__ if values else "NoneType",
            semantic_type=st,
            cardinality=len(unique_values),
            sample=unique_values[:5],
            min_value=float(min(values)) if st == "numeric" and values else None,
            max_value=float(max(values)) if st == "numeric" and values else None,
        )
        features.append(feat)

    return DataShape(
        row_count=len(rows),
        columns=features,
        shape_pattern=_infer_pattern(features),
    )


def _to_hashable(v: Any) -> Any:
    """把 list/dict 等不可哈希值转成可哈希形式,用于 set 去重。"""
    if isinstance(v, (list, tuple)):
        return tuple(v)
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    return v
