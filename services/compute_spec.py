"""ComputeSpec —— "Text2SQL 的无 SQL 版本"。

LLM 输出 ComputeSpec(JSON 结构化)→ Python 用 pandas 执行 → rows。
跟你 DW 路径生成 SQL 字符串等价,只是把 SQL 换成机器可校验的结构化 spec。
"""
from __future__ import annotations

import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field


# ───────────────────────────────────────────────────────
# Pydantic 模型
# ───────────────────────────────────────────────────────

FilterOp = Literal[
    "eq", "ne",
    "in", "not_in",
    "gt", "gte", "lt", "lte", "between",
    "contains", "icontains", "all_tokens", "startswith", "endswith", "regex",
    "is_null", "not_null",
]

AggFunc = Literal["sum", "mean", "count", "min", "max", "median", "nunique", "first", "last"]


class Filter(BaseModel):
    col: str
    op: FilterOp
    # 单值 op(eq/ne/gt/gte/lt/lte/contains/icontains/all_tokens/startswith/endswith/regex)
    value: Any | None = None
    # 多值 op(in/not_in/between)
    values: list[Any] | None = None


class Aggregation(BaseModel):
    col: str                              # 要聚合的列(count 时可以传 "*" 或随便填,内部会处理)
    func: AggFunc
    alias: str | None = None              # 结果列名;不指定 → 用 "{col}_{func}"


class OrderBy(BaseModel):
    col: str
    dir: Literal["asc", "desc"] = "asc"


class ComputeSpec(BaseModel):
    """LLM 输出的结构化查询意图。"""
    sheet: str                            # 操作哪个 sheet
    filters: list[Filter] = Field(default_factory=list)
    groupby: list[str] = Field(default_factory=list)
    aggregations: list[Aggregation] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int | None = None
    # 给 LLM 留个解释字段,后端日志/调试用,前端不展示
    reason: str = ""


# ───────────────────────────────────────────────────────
# 执行器
# ───────────────────────────────────────────────────────

def _to_compare_value(df: pd.DataFrame, col: str, v: Any) -> Any:
    """对 datetime 列,把字符串值转成 Timestamp,确保比较成立。"""
    if col not in df.columns:
        return v
    if pd.api.types.is_datetime64_any_dtype(df[col]):
        try:
            return pd.to_datetime(v)
        except Exception:
            return v
    return v


def _apply_filter(df: pd.DataFrame, f: Filter) -> pd.DataFrame:
    """单个 filter 应用到 df 上,返回筛选后的新 df。"""
    col = f.col
    if col not in df.columns:
        raise ValueError(f"filter 引用的列 '{col}' 不存在于 sheet 中")

    if f.op == "is_null":
        return df[df[col].isna()]
    if f.op == "not_null":
        return df[df[col].notna()]

    # 多值 op
    if f.op == "in":
        vals = [_to_compare_value(df, col, v) for v in (f.values or [])]
        return df[df[col].isin(vals)]
    if f.op == "not_in":
        vals = [_to_compare_value(df, col, v) for v in (f.values or [])]
        return df[~df[col].isin(vals)]
    if f.op == "between":
        if not f.values or len(f.values) != 2:
            raise ValueError("between 需要 values=[lo, hi]")
        lo = _to_compare_value(df, col, f.values[0])
        hi = _to_compare_value(df, col, f.values[1])
        return df[df[col].between(lo, hi)]

    # 单值 op
    v = _to_compare_value(df, col, f.value)
    if f.op == "eq":   return df[df[col] == v]
    if f.op == "ne":   return df[df[col] != v]
    if f.op == "gt":   return df[df[col] >  v]
    if f.op == "gte":  return df[df[col] >= v]
    if f.op == "lt":   return df[df[col] <  v]
    if f.op == "lte":  return df[df[col] <= v]

    # 字符串 op(强制把列转成字符串再操作,避免数值列报错)
    s = df[col].astype(str)
    if f.op == "contains":
        return df[s.str.contains(str(f.value), case=True, na=False, regex=False)]
    if f.op == "icontains":
        return df[s.str.contains(str(f.value), case=False, na=False, regex=False)]
    if f.op == "startswith":
        return df[s.str.startswith(str(f.value), na=False)]
    if f.op == "endswith":
        return df[s.str.endswith(str(f.value), na=False)]
    if f.op == "regex":
        return df[s.str.contains(str(f.value), case=True, na=False, regex=True)]
    if f.op == "all_tokens":
        # 分词,任意顺序、忽略大小写、所有 token 都得在
        tokens = [t for t in re.split(r"\s+", str(f.value).strip().lower()) if t]
        if not tokens:
            return df
        sub = df
        for tok in tokens:
            sub_s = sub[col].astype(str).str.lower()
            sub = sub[sub_s.str.contains(re.escape(tok), na=False, regex=True)]
        return sub

    raise ValueError(f"未知 op: {f.op}")


def _alias_for(a: Aggregation) -> str:
    return a.alias or f"{a.col}_{a.func}"


def _apply_aggregations(df: pd.DataFrame, spec: ComputeSpec) -> pd.DataFrame:
    """执行 groupby + agg,或不分组的整列聚合。"""
    if not spec.aggregations and not spec.groupby:
        # 没聚合也没分组:相当于纯过滤,直接返回 filtered df
        return df

    # 有 groupby:走 pandas .agg
    if spec.groupby:
        for k in spec.groupby:
            if k not in df.columns:
                raise ValueError(f"groupby 引用的列 '{k}' 不存在")
        if not spec.aggregations:
            # 只 groupby 不 agg:相当于 distinct
            return df[spec.groupby].drop_duplicates().reset_index(drop=True)

        # 构造 named aggregations(干净的列命名,支持 alias)
        named: dict[str, tuple[str, str]] = {}
        for a in spec.aggregations:
            if a.col != "*" and a.col not in df.columns:
                raise ValueError(f"aggregation 引用的列 '{a.col}' 不存在")
            target_col = a.col if a.col != "*" else spec.groupby[0]  # count(*) 借任一列
            named[_alias_for(a)] = (target_col, a.func)
        agg_kwargs = {alias: pd.NamedAgg(column=c, aggfunc=f) for alias, (c, f) in named.items()}
        return df.groupby(spec.groupby, dropna=False).agg(**agg_kwargs).reset_index()

    # 没 groupby 但有 aggregations:整列聚合 → 单行 DataFrame
    row: dict[str, Any] = {}
    for a in spec.aggregations:
        if a.col != "*" and a.col not in df.columns:
            raise ValueError(f"aggregation 引用的列 '{a.col}' 不存在")
        col = a.col if a.col != "*" else df.columns[0]
        series = df[col]
        if a.func == "count":
            row[_alias_for(a)] = int(series.count())
        elif a.func == "nunique":
            row[_alias_for(a)] = int(series.nunique())
        elif a.func == "first":
            row[_alias_for(a)] = series.iloc[0] if len(series) else None
        elif a.func == "last":
            row[_alias_for(a)] = series.iloc[-1] if len(series) else None
        else:
            row[_alias_for(a)] = series.agg(a.func)
    return pd.DataFrame([row])


def _apply_order_limit(df: pd.DataFrame, spec: ComputeSpec) -> pd.DataFrame:
    if spec.order_by:
        # 校验列存在
        cols = []
        ascs = []
        for o in spec.order_by:
            if o.col not in df.columns:
                raise ValueError(f"order_by 引用的列 '{o.col}' 不存在(可能是 alias 没对上)")
            cols.append(o.col)
            ascs.append(o.dir == "asc")
        df = df.sort_values(by=cols, ascending=ascs, kind="mergesort")
    if spec.limit is not None and spec.limit > 0:
        df = df.head(spec.limit)
    return df


def _to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → list[dict],NaN/NaT → None,numpy scalar → Python 原生。"""
    if df.empty:
        return []
    # NaN/NaT → None
    cleaned = df.astype(object).where(pd.notnull(df), None)
    records = cleaned.to_dict("records")
    # numpy scalar → Python 原生(防止 JSON 序列化时出 int64/Timestamp 这种)
    out = []
    for r in records:
        new_r = {}
        for k, v in r.items():
            if v is None:
                new_r[k] = None
            elif hasattr(v, "isoformat"):                # Timestamp / datetime
                new_r[k] = v.isoformat()
            elif hasattr(v, "item"):                     # numpy scalar
                try:
                    new_r[k] = v.item()
                except Exception:
                    new_r[k] = str(v)
            else:
                new_r[k] = v
        out.append(new_r)
    return out


def execute_spec(df: pd.DataFrame, spec: ComputeSpec) -> list[dict[str, Any]]:
    """ComputeSpec → pandas → list[dict] rows。下游(chart_agent / interpret_result)直接吃。

    步骤:filter → groupby+agg(或整列聚合)→ order_by → limit → to_records。
    """
    if df.empty:
        return []
    work = df
    for f in spec.filters:
        work = _apply_filter(work, f)
        if work.empty:
            return []
    work = _apply_aggregations(work, spec)
    work = _apply_order_limit(work, spec)
    return _to_rows(work)
