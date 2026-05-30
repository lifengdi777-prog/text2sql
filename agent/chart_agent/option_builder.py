"""确定性地把 SQL 结果集构造成 ECharts option(后端版)。

与前端 wenshu-frontend/src/lib/chartBuilder.ts 是同一套逻辑：
LLM 只负责「选 chart_type」,长表透视、填 series.data 这类确定性变换全在这里用代码做。
好处:省掉 LLM 生成大段 JSON(快),且不会把数据透视错(稳)。

构造的 dict 同时含 ECharts option 键(title/series/...)与元字段(chart_type),
前端 ChartPanel 渲染前会剥掉元字段再 setOption。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _num(v: Any) -> float:
    """转数值,失败给 0(对齐前端 num())。"""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _looks_numeric(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float, Decimal)):
        return True
    if isinstance(v, str) and v.strip() != "":
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _unique_sorted(values: list[Any]) -> list[Any]:
    """去重 + 排序:全数值按数值升序,否则按字符串升序(对齐前端 uniqueSorted())。"""
    uniq = list(dict.fromkeys(values))  # 去重,保持首次出现顺序
    if uniq and all(_looks_numeric(v) for v in uniq):
        return sorted(uniq, key=lambda x: float(x))
    return sorted(uniq, key=lambda x: str(x))


def build_chart_option(
    chart_type: str,
    rows: list[dict[str, Any]],
    field_map: dict[str, Any],
    title: str,
) -> dict[str, Any]:
    """按 chart_type + 字段映射,把 rows 构造成 ECharts option(含 chart_type 元字段)。"""
    dim = field_map.get("dimension") or ""
    measure = field_map.get("measure") or ""
    series_field = field_map.get("series") or ""
    title_obj = {"text": title, "left": "center"}

    # 饼图:按数值降序,data 为 [{name, value}]
    if chart_type == "pie":
        ordered = sorted(rows, key=lambda r: _num(r.get(measure)), reverse=True)
        return {
            "chart_type": "pie",
            "title": title_obj,
            "tooltip": {"trigger": "item", "formatter": "{b}<br/>{c} ({d}%)"},
            "legend": {"orient": "vertical", "left": "left", "top": "middle", "type": "scroll"},
            "series": [{
                "name": title,
                "type": "pie",
                "radius": ["38%", "68%"],
                "center": ["58%", "52%"],
                "avoidLabelOverlap": True,
                "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 2},
                "label": {"show": True, "formatter": "{b}\n{d}%"},
                "data": [{"name": str(r.get(dim, "")), "value": _num(r.get(measure))} for r in ordered],
            }],
        }

    # 柱状图:单指标按值降序(排行);多指标(measures>1)并排分组柱,保持原始行序
    if chart_type == "bar":
        measures = field_map.get("measures") or ([measure] if measure else [])
        multi = len(measures) > 1
        ordered = list(rows) if multi else sorted(rows, key=lambda r: _num(r.get(measure)), reverse=True)
        x = [r.get(dim) for r in ordered]
        return {
            "chart_type": "bar",
            "title": title_obj,
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "legend": {"data": measures, "top": "bottom", "type": "scroll"},
            "grid": {"left": "3%", "right": "4%", "bottom": "12%", "containLabel": True},
            "xAxis": {"type": "category", "data": x, "name": dim,
                      "axisLabel": {"interval": 0, "rotate": 30 if len(x) > 6 else 0}},
            "yAxis": {"type": "value", "name": "" if multi else measure},
            "series": [{"name": m, "type": "bar", "data": [_num(r.get(m)) for r in ordered]} for m in measures],
        }

    # 折线图:按维度(时间)升序,单系列
    if chart_type == "line":
        dim_numeric = all(_looks_numeric(r.get(dim)) for r in rows) if rows else False
        ordered = sorted(rows, key=(lambda r: _num(r.get(dim))) if dim_numeric else (lambda r: str(r.get(dim))))
        return {
            "chart_type": "line",
            "title": title_obj,
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": [r.get(dim) for r in ordered], "name": dim},
            "yAxis": {"type": "value", "name": measure},
            "series": [{"name": measure, "type": "line", "smooth": True,
                        "data": [_num(r.get(measure)) for r in ordered]}],
        }

    # 多线 / 堆叠柱:长表透视成宽表
    if chart_type in ("multi_line", "stacked_bar"):
        is_stacked = chart_type == "stacked_bar"
        x_vals = _unique_sorted([r.get(dim) for r in rows if r.get(dim) is not None])
        series_names: list[str] = []
        for r in rows:
            s = str(r.get(series_field, ""))
            if s != "" and s not in series_names:
                series_names.append(s)
        lookup: dict[str, float] = {}
        for r in rows:
            lookup[f"{r.get(dim)}||{r.get(series_field)}"] = _num(r.get(measure))

        series_list = []
        for s in series_names:
            data = []
            for x in x_vals:
                key = f"{x}||{s}"
                data.append(lookup[key] if key in lookup else (0 if is_stacked else None))
            item = {"name": s, "type": "bar" if is_stacked else "line", "data": data}
            if is_stacked:
                item["stack"] = "total"
            else:
                item["smooth"] = True
            series_list.append(item)

        return {
            "chart_type": chart_type,
            "title": title_obj,
            "tooltip": {"trigger": "axis", **({"axisPointer": {"type": "shadow"}} if is_stacked else {})},
            "legend": {"data": series_names, "top": "bottom", "type": "scroll"},
            "grid": {"left": "3%", "right": "4%", "bottom": "15%", "containLabel": True},
            "xAxis": {"type": "category", "data": x_vals, "name": dim,
                      **({} if is_stacked else {"boundaryGap": False})},
            "yAxis": {"type": "value"},
            "series": series_list,
        }

    # 兜底:table 标记(前端按 rows 渲染表格)
    return {"chart_type": "table", "title": title_obj}
