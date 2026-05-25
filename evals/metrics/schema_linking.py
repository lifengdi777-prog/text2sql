"""
Schema Linking Recall: 评估召回链路是否覆盖了 gold SQL 用到的表/列。

这个指标的最大价值:把 Text2SQL 系统拆成"召回"和"生成"两段独立诊断。
- recall 高,EX 低 → LLM 生成有问题(prompt / 模型 / 上下文太长)
- recall 低 → 召回链路有问题(关键词、向量、ES、过滤)

从 gold SQL 抽出"应该被召回的表/列",和 state 中实际召回结果做交集。
注意:gold SQL 里写的列名是物理列名,需要和 ColumnInfo.name 对齐。
"""
from __future__ import annotations

from typing import Any

from sqlglot import exp, parse_one


def extract_required_schema(gold_sql: str, dialect: str = "mysql") -> tuple[set[str], set[str]]:
    """
    从 gold SQL 抽出 (表名集合, 列名集合)。
    列名只取物理列名(不带表前缀)。
    """
    try:
        tree = parse_one(gold_sql, read=dialect)
    except Exception:
        return set(), set()
    if tree is None:
        return set(), set()

    tables = {t.name for t in tree.find_all(exp.Table) if t.name}
    columns = {c.name for c in tree.find_all(exp.Column) if c.name}
    return tables, columns


def schema_linking_recall(
    gold_sql: str,
    recalled_table_names: set[str],
    recalled_column_names: set[str],
    gold_tables_hint: list[str] | None = None,
    gold_columns_hint: list[str] | None = None,
) -> dict[str, Any]:
    """
    计算召回 recall。

    Args:
        gold_sql: 黄金 SQL,用于自动抽取必要表/列
        recalled_table_names: agent 实际召回/过滤后保留的表名集合
        recalled_column_names: agent 实际召回的列名集合
        gold_tables_hint: yaml 里显式标注的必要表(优先于 SQL 抽取)
        gold_columns_hint: yaml 里显式标注的必要列

    返回:
        {
            "table_recall": float,         # 0~1
            "column_recall": float,         # 0~1
            "missing_tables": [...],
            "missing_columns": [...],
            "required_tables": [...],
            "required_columns": [...],
        }
    """
    auto_tables, auto_columns = extract_required_schema(gold_sql)

    # YAML hint 优先,没有就用从 gold SQL 自动抽的
    required_tables = set(gold_tables_hint) if gold_tables_hint else auto_tables
    required_columns = set(gold_columns_hint) if gold_columns_hint else auto_columns

    if required_tables:
        hit_tables = required_tables & recalled_table_names
        table_recall = len(hit_tables) / len(required_tables)
        missing_tables = sorted(required_tables - recalled_table_names)
    else:
        table_recall = 1.0
        missing_tables = []

    if required_columns:
        hit_columns = required_columns & recalled_column_names
        column_recall = len(hit_columns) / len(required_columns)
        missing_columns = sorted(required_columns - recalled_column_names)
    else:
        column_recall = 1.0
        missing_columns = []

    return {
        "table_recall": round(table_recall, 4),
        "column_recall": round(column_recall, 4),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "required_tables": sorted(required_tables),
        "required_columns": sorted(required_columns),
    }
