"""
Exact Set Match: 用 sqlglot parse 成 AST,比较结构等价性。

比 Execution Accuracy 更严格——即使两条 SQL 跑出同样结果,
如果用了不同的表 / 列 / 聚合方式,EM 也会判为不等价。

主要用途:
- 当 gold SQL 跑不通(比如 schema 改了)时,EM 仍然能给一个信号
- 看 LLM 有没有用对正确的表(避免靠"碰巧返回空集"骗过 EX)
"""
from __future__ import annotations

from typing import Any

from sqlglot import exp, parse_one


def extract_skeleton(sql: str, dialect: str = "mysql") -> dict[str, set[str]]:
    """
    从 SQL 抽出骨架特征,用于结构等价比较。

    返回:
        {
            "tables": {表名集合},
            "columns": {列名集合(不带表前缀)},
            "aggregates": {聚合函数小写名集合},
            "has_group_by": {"true"/"false"},
            "has_order_by": {"true"/"false"},
            "has_limit": {"true"/"false"},
            "has_distinct": {"true"/"false"},
            "has_window": {"true"/"false"},
        }
    """
    try:
        tree = parse_one(sql, read=dialect)
    except Exception:
        # parse 失败也是一种"骨架",用特殊标记
        return {
            "tables": set(),
            "columns": set(),
            "aggregates": set(),
            "has_group_by": {"parse_error"},
            "has_order_by": set(),
            "has_limit": set(),
            "has_distinct": set(),
            "has_window": set(),
        }

    if tree is None:
        return {k: set() for k in [
            "tables", "columns", "aggregates",
            "has_group_by", "has_order_by", "has_limit", "has_distinct", "has_window"
        ]}

    tables = {t.name for t in tree.find_all(exp.Table) if t.name}
    columns = {c.name for c in tree.find_all(exp.Column) if c.name}

    agg_types = (exp.Sum, exp.Count, exp.Avg, exp.Max, exp.Min)
    aggregates = {a.__class__.__name__.lower() for a in tree.find_all(agg_types)}

    def _flag(matched: bool) -> set[str]:
        return {"true"} if matched else {"false"}

    return {
        "tables": tables,
        "columns": columns,
        "aggregates": aggregates,
        "has_group_by": _flag(bool(list(tree.find_all(exp.Group)))),
        "has_order_by": _flag(bool(list(tree.find_all(exp.Order)))),
        "has_limit": _flag(bool(list(tree.find_all(exp.Limit)))),
        "has_distinct": _flag(bool(list(tree.find_all(exp.Distinct)))),
        "has_window": _flag(bool(list(tree.find_all(exp.Window)))),
    }


def exact_match(gold_sql: str, pred_sql: str | None, dialect: str = "mysql") -> dict[str, Any]:
    """
    判断两条 SQL 是否结构等价,并返回 per-feature 的对错明细。

    返回:
        {
            "match": bool,             # 全部特征都对才为 True
            "table_match": bool,
            "column_match": bool,
            "aggregate_match": bool,
            "structure_match": bool,   # group/order/limit/distinct/window 是否一致
        }
    """
    if not pred_sql or not pred_sql.strip():
        return {
            "match": False, "table_match": False, "column_match": False,
            "aggregate_match": False, "structure_match": False,
        }

    g = extract_skeleton(gold_sql, dialect)
    p = extract_skeleton(pred_sql, dialect)

    table_match = g["tables"] == p["tables"]
    column_match = g["columns"] == p["columns"]
    aggregate_match = g["aggregates"] == p["aggregates"]
    structure_match = all(
        g[k] == p[k]
        for k in ("has_group_by", "has_order_by", "has_limit", "has_distinct", "has_window")
    )
    return {
        "match": table_match and column_match and aggregate_match and structure_match,
        "table_match": table_match,
        "column_match": column_match,
        "aggregate_match": aggregate_match,
        "structure_match": structure_match,
    }
