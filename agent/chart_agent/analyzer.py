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


def _to_hashable(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return tuple(v)
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    return v


def analyze(rows: list[dict[str, Any]]) -> DataShape:
    if not rows:
        return DataShape(row_count=0, columns=[])

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
    )


# 前端 ECharts 已注册、可渲染的图表类型(LLM 选型时的全集)。
# 加新类型时:前端注册对应图表族 + option_builder 支持 + 在此登记。
SUPPORTED_CHART_TYPES = ["line", "multi_line", "bar", "stacked_bar", "pie", "table"]

# 可读性硬上限(enforce_limits 用真实基数事后校验)
_PIE_MAX_CARD = 8
_BAR_MAX_CARD = 8
_SERIES_MAX_CARD = 8   # 多系列(multi_line/stacked_bar)的系列数上限

# 比率/均价类指标的列名特征:这类是「逐组各自的比率」,不是可加的份额
_RATIO_NAME_HINTS = (
    "率", "比例", "占比", "百分比", "均价", "单价", "客单价",
    "rate", "ratio", "pct", "percent", "%", "arpu",
)


def _is_ratio_name(name: str) -> bool:
    nl = str(name).lower()
    return any(h in nl for h in _RATIO_NAME_HINTS)


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


def enforce_limits(chart_type: str, field_map: dict[str, Any], shape: DataShape | None) -> tuple[str, str]:
    """LLM 选完图后,用**真实基数**做硬限制兜底校验,违反则降级。返回 (chart_type, 降级原因)。

    它只查精确事实(基数够不够小、必需的映射列齐不齐),**不做任何语义猜测**,
    所以不会犯"时间/分类判错"那类错误 —— 这是规则唯一仍该把守的红线。
    """
    if chart_type == "table" or shape is None:
        return chart_type, ""

    card = {c.name: c.cardinality for c in shape.columns}
    dim = field_map.get("dimension")
    series = field_map.get("series")
    measure = field_map.get("measure")
    measures = field_map.get("measures")

    # 1. 缺必需映射 → 画不了,降级表格
    if not dim:
        return "table", "缺少横轴/分类列映射,降级为表格"
    if chart_type == "bar":
        if not (measure or measures):
            return "table", "柱图缺少数值列,降级为表格"
    elif not measure:
        return "table", f"{chart_type} 缺少数值列,降级为表格"

    # 2. 多系列却没指定分组列 → 退回单系列
    if chart_type == "multi_line" and not series:
        return "line", "未指定分组列,降级为单线 line"
    if chart_type == "stacked_bar" and not series:
        return "bar", "未指定分组列,降级为普通 bar"

    # 3. 多系列分组过多 → 表格(线/堆叠段太多看不清)
    if chart_type in ("multi_line", "stacked_bar") and series and card.get(series, 0) > _SERIES_MAX_CARD:
        return "table", f"分组列「{series}」有 {card[series]} 个值 > {_SERIES_MAX_CARD},降级为表格"

    # 3.5 单维度图(line/bar/pie)要求"一行一个维度值":若行数 > 维度基数,
    #     说明还有未透视的第二维(如 工厂×产品类别),单维度图会按行错画(扇区/柱子重复)。
    #     → 有分组列走多系列(stacked_bar/multi_line),否则降表格。
    if chart_type in ("line", "bar", "pie"):
        dim_card = card.get(dim, 0)
        if dim_card and shape.row_count > dim_card:
            if series and card.get(series, 0) <= _SERIES_MAX_CARD:
                fallback = "multi_line" if chart_type == "line" else "stacked_bar"
                return fallback, (f"每个「{dim}」对应多行(行数 {shape.row_count} > 基数 {dim_card}),"
                                  f"存在第二维度,改用 {fallback}")
            return "table", f"每个「{dim}」对应多行(行数 {shape.row_count} > 基数 {dim_card}),单维度图会错画,降级为表格"

    # 4. 柱图横轴类别过多 → 表格
    if chart_type in ("bar", "stacked_bar") and card.get(dim, 0) > _BAR_MAX_CARD:
        return "table", f"横轴「{dim}」有 {card[dim]} 类 > {_BAR_MAX_CARD},降级为表格"

    # 5. 饼图:扇区过多 → 柱图;指标不是可加占比(率/均值) → 柱图
    if chart_type == "pie":
        if card.get(dim, 0) > _PIE_MAX_CARD:
            return "bar", f"饼图扇区 {card[dim]} 个 > {_PIE_MAX_CARD},降级为柱图"
        measure_feat = next((c for c in shape.columns if c.name == measure), None)
        if not _pie_meaningful(measure_feat):
            return "bar", "该指标不是可加的占比(率/均值类),饼图无意义,降级为柱图"

    return chart_type, ""
