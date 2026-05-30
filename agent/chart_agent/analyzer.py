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
            sum_value=float(sum(values)) if st == "numeric" and values else None,
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

# 比率/均价类指标的列名特征:这类是「逐组各自的比率」,不是可加的份额
_RATIO_NAME_HINTS = (
    "率", "比例", "占比", "百分比", "均价", "单价", "客单价",
    "rate", "ratio", "pct", "percent", "%", "arpu",
)


def _is_ratio_name(name: str) -> bool:
    nl = str(name).lower()
    return any(h in nl for h in _RATIO_NAME_HINTS)


def _pick_measure(numeric: list[ColumnFeature]) -> ColumnFeature | None:
    """从多个数值列里挑「主指标」。

    SQL 结果常含中间量(总数/分子)+ 最终派生指标(比率)。用户问的通常是那个比率,
    所以**优先选比率/均价类命名的列**;没有就退回第一个数值列。
    """
    if not numeric:
        return None
    for c in numeric:
        if _is_ratio_name(c.name):
            return c
    return numeric[0]


def _pie_meaningful(measure: ColumnFeature | None) -> bool:
    """饼图是否成立:饼图表达「部分占整体、加起来=100%」。

    - 可加指标(销售额/数量…)→ 各分类的份额有意义,成立;
    - 比率/均价类(维护比例/不良率/客单价…)→ 逐组各自的比率,加起来无整体含义,
      **除非** 各组之和构成一个整体:百分比刻度 ≈100,或小数刻度 ≈1.0(真·占比列,如「销售额占比」)。
    """
    if measure is None:
        return False
    if not _is_ratio_name(measure.name):
        return True
    s = measure.sum_value
    if s is None:
        return False
    # 占比列可能是 0~1 小数(和≈1)或百分数(和≈100),两种都算真·占比
    return (0.95 <= s <= 1.05) or (95.0 <= s <= 105.0)


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

    # 单行(或空)结果画不成趋势/对比图(每列基数都是 1),只能表格。
    # 单条明细查询就落在这里,避免误给 multi_line / stacked_bar 等切换项。
    if shape.row_count <= 1:
        return ["table"]

    temporal = [c for c in shape.columns if c.semantic_type == "temporal"]
    categorical = [c for c in shape.columns if c.semantic_type == "categorical"]
    numeric = [c for c in shape.columns if c.semantic_type == "numeric"]
    n_temp, n_cat, n_num = len(temporal), len(categorical), len(numeric)

    types: list[str] = []
    dim_cols = temporal + categorical

    # 1 维度 + ≥1 数值 → line/bar/pie 这组(多数值时用 _pick_measure 选主指标)
    if len(dim_cols) == 1 and n_num >= 1:
        dim = dim_cols[0]
        measure = _pick_measure(numeric)
        if dim.semantic_type == "temporal":
            types.append("line")
        if dim.cardinality <= _BAR_MAX_CARD:
            types.append("bar")
        # 饼图:维度基数够小,且主指标确实是「可加份额/真·占比」(比率类不给饼图,避免误导)
        if dim.cardinality <= _PIE_MAX_CARD and _pie_meaningful(measure):
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


def chart_field_map(shape: DataShape | None) -> dict[str, Any]:
    """给前端本地构图用的字段映射:dimension(X轴) / measure(主数值) / measures(多指标分组柱) / series。"""
    if shape is None:
        return {}
    temporal = [c.name for c in shape.columns if c.semantic_type == "temporal"]
    categorical = [c.name for c in shape.columns if c.semantic_type == "categorical"]
    numeric = [c for c in shape.columns if c.semantic_type == "numeric"]

    fm: dict[str, Any] = {}
    measure = _pick_measure(numeric)
    if measure is not None:
        # 主指标选比率/派生列(用户真正问的),而不是第一个数值列(常是中间量)
        fm["measure"] = measure.name

    # 多个「同量纲」数值指标(如 设备总数 + 维护设备数)→ 提供分组柱所需的指标列表,
    # 前端柱状图据此并排画多组。带比率列时不分组(比率量纲不同,且它才是焦点)。
    ratio_cols = [c for c in numeric if _is_ratio_name(c.name)]
    if len(numeric) >= 2 and not ratio_cols:
        fm["measures"] = [c.name for c in numeric]

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
