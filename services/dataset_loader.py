"""数据集加载层:从 MySQL 拿 schema(带短期缓存),并把 schema_json 渲染成给 LLM 看的 markdown。

数据本体不再在这里加载/缓存 —— 查询时由 DuckDB 直接读 parquet(见 services/duckdb_exec.py),
不把整表 load 进进程内存,也不缓存 DataFrame(更省内存、更贴近列存引擎用法)。
"""
from __future__ import annotations

from typing import Any

from cachetools import TTLCache

from repositories.upload import UploadDatasetRepository
from services.excel_ingest import get_session_factory


# Schema 缓存:小对象(一行元信息 + schema_json),按条数限容;TTL 短,避免读到旧 status
_SCHEMA_CACHE: TTLCache = TTLCache(maxsize=100, ttl=300)


# ───────────────────────────────────────────
# 加载
# ───────────────────────────────────────────

async def get_dataset_info(dataset_id: int, with_lineage: bool = False) -> dict[str, Any] | None:
    """从 MySQL 拉数据集元信息 + schema_json,带短期缓存。

    返回 None 表示数据集不存在;返回 dict 包含:
      dataset_id / name / folder_path / status / schema(JSON)

    with_lineage:
      - False(默认,**问数**用):只读轻量 schema(列详情),**不加载血缘** → 又小又快,缓存的也是轻量版。
      - True(**智能助手**编辑/导出用):额外从单独的 lineage_json 列读血缘,拼回 schema.sheets[*].lineage,
        供保样式回写。血缘不进缓存,不污染问数那份轻量 info。
    """
    base = _SCHEMA_CACHE.get(dataset_id)
    if base is None:
        Session = get_session_factory()
        async with Session() as session:
            repo = UploadDatasetRepository(session)
            ds = await repo.get(dataset_id)
            if ds is None:
                return None
            base = {
                "dataset_id": ds.id,
                "name": ds.name,
                "folder_path": ds.folder_path,
                "status": ds.status,
                "schema": ds.schema_json,
            }
        _SCHEMA_CACHE[dataset_id] = base

    if not with_lineage:
        return base
    return await _attach_lineage(dataset_id, base)


async def _attach_lineage(dataset_id: int, base: dict[str, Any]) -> dict[str, Any]:
    """把血缘(单独列)读出来拼回 schema.sheets[*].lineage。返回**新 dict**,不改缓存的轻量 base。"""
    Session = get_session_factory()
    async with Session() as session:
        lineage_map = await UploadDatasetRepository(session).get_lineage(dataset_id) or {}
    schema = base.get("schema") or {}
    sheets = schema.get("sheets") or {}
    new_sheets = {name: {**sinfo, "lineage": lineage_map.get(name)} for name, sinfo in sheets.items()}
    return {**base, "schema": {**schema, "sheets": new_sheets}}


def invalidate_cache(dataset_id: int) -> None:
    """删除/更新数据集时调,清掉 schema 缓存防止陈旧。"""
    _SCHEMA_CACHE.pop(dataset_id, None)


# ───────────────────────────────────────────
# Schema → LLM 看的 markdown
# ───────────────────────────────────────────

def _render_column_detail(col: dict[str, Any]) -> str:
    """渲染单列的"详情"信息(给 LLM 看,影响它写 SQL 的决策)。"""
    parts: list[str] = []
    sem = col.get("semantic_type", "categorical")
    cardinality = col.get("cardinality", 0)
    null_count = col.get("null_count", 0)

    if sem == "numeric":
        if "min" in col and "max" in col:
            parts.append(f"范围 {col['min']} ~ {col['max']}")
        if col.get("mean") is not None:
            parts.append(f"均值 {col['mean']}")

    elif sem == "temporal":
        if "min" in col and "max" in col:
            parts.append(f"范围 {col['min']} ~ {col['max']}")

    else:  # categorical
        if not col.get("is_high_cardinality") and "values" in col:
            vals = col["values"]
            # 全枚举(小基数列,LLM 可以用 = / IN 精确匹配)
            vals_str = ", ".join(str(v) for v in vals)
            parts.append(f"{cardinality} 个值:[{vals_str}]")
        elif "top_k" in col:
            top = col["top_k"]   # 入库时已截到 _TOP_K(5)个,这里直接全展示
            top_str = ", ".join(str(v) for v in top)
            parts.append(
                f"{cardinality} 个值(高基数)。"
                f"样例:[{top_str}]。**精确值可能不在样例里 —— WHERE 用 ILIKE '%关键词%' 模糊匹配,不要用 =**"
            )

    if null_count > 0:
        parts.append(f"空值 {null_count}")

    return ";  ".join(parts) if parts else "-"


def render_schema_for_prompt(schema: dict[str, Any]) -> str:
    """schema_json → LLM 看的 markdown 段落。

    例:
      ### Sheet "生产明细"(共 8 行)
      可用列:

      | 列名 | 类型 | 详情 |
      |---|---|---|
      | 工厂 | categorical | 4 个值:[华东工厂, 华北工厂, 华南工厂, 西南工厂] |
      | 产量 | numeric | 范围 1890 ~ 13478;均值 6010.0 |
      ...
    """
    if not schema or "sheets" not in schema:
        return "(数据集 schema 为空)"

    lines: list[str] = []
    for sheet_name, sheet_info in schema["sheets"].items():
        row_count = sheet_info.get("row_count", 0)
        lines.append(f"### Sheet \"{sheet_name}\"(共 {row_count} 行)")
        lines.append("")
        lines.append("| 列名 | 类型 | 详情 |")
        lines.append("|---|---|---|")
        for col in sheet_info.get("columns", []):
            name = col.get("name", "")
            sem = col.get("semantic_type", "")
            detail = _render_column_detail(col)
            lines.append(f"| `{name}` | {sem} | {detail} |")
        lines.append("")

    return "\n".join(lines)
