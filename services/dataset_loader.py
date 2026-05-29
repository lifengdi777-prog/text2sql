"""数据集加载层:从 MySQL 拿 schema,从 parquet 拿 DataFrame,都带 TTL 缓存。

缓存策略:
  - Schema(MySQL 行,小):TTL 300s,最多 100 个
  - DataFrame(parquet,大):TTL 1800s(30 分钟),最多 200 个 (dataset, sheet)
  - 单次 LLM 请求会反复读 schema + 至少一个 DF,缓存命中能省一次 MySQL+parquet IO

Schema 渲染:把 schema_json 转成给 LLM 看的 markdown(列名/类型/详情)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from cachetools import TTLCache

from repositories.upload import UploadDatasetRepository
from services.excel_ingest import get_session_factory


# ───────────────────────────────────────────
# 缓存
# ───────────────────────────────────────────

# DataFrame 缓存:key = (dataset_id, sheet_name)
_DF_CACHE: TTLCache = TTLCache(maxsize=200, ttl=1800)

# Schema 缓存:key = dataset_id,value = 完整 dataset 信息 dict
_SCHEMA_CACHE: TTLCache = TTLCache(maxsize=100, ttl=300)


# ───────────────────────────────────────────
# 加载
# ───────────────────────────────────────────

async def get_dataset_info(dataset_id: int) -> dict[str, Any] | None:
    """从 MySQL 拉数据集元信息 + schema_json,带短期缓存。

    返回 None 表示数据集不存在;返回 dict 包含:
      dataset_id / name / folder_path / status / schema(JSON)
    """
    if dataset_id in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[dataset_id]

    Session = get_session_factory()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        ds = await repo.get(dataset_id)
        if ds is None:
            return None
        info = {
            "dataset_id": ds.id,
            "name": ds.name,
            "folder_path": ds.folder_path,
            "status": ds.status,
            "schema": ds.schema_json,
        }
    _SCHEMA_CACHE[dataset_id] = info
    return info


async def load_sheet_df(dataset_id: int, sheet_name: str) -> pd.DataFrame:
    """加载某 sheet 的 DataFrame(从 parquet),带 LRU 缓存。"""
    key = (dataset_id, sheet_name)
    if key in _DF_CACHE:
        return _DF_CACHE[key]

    info = await get_dataset_info(dataset_id)
    if info is None:
        raise ValueError(f"数据集 {dataset_id} 不存在")
    if info["status"] != "ready":
        raise ValueError(f"数据集 {dataset_id} 状态={info['status']},不可查询")

    schema = info["schema"] or {}
    sheets = schema.get("sheets", {})
    if sheet_name not in sheets:
        available = list(sheets.keys())
        raise ValueError(f"sheet '{sheet_name}' 不存在于数据集 {dataset_id}(可选:{available})")

    parquet_file = sheets[sheet_name].get("parquet_file")
    if not parquet_file:
        raise ValueError(f"sheet '{sheet_name}' 缺 parquet_file 字段(schema 损坏?)")

    path = Path(info["folder_path"]) / parquet_file
    if not path.exists():
        raise FileNotFoundError(f"parquet 文件不存在:{path}")

    df = pd.read_parquet(path)
    _DF_CACHE[key] = df
    return df


def invalidate_cache(dataset_id: int) -> None:
    """删除/更新数据集时调,清掉对应缓存防止陈旧。"""
    _SCHEMA_CACHE.pop(dataset_id, None)
    stale_keys = [k for k in _DF_CACHE.keys() if k[0] == dataset_id]
    for k in stale_keys:
        _DF_CACHE.pop(k, None)


# ───────────────────────────────────────────
# Schema → LLM 看的 markdown
# ───────────────────────────────────────────

def _render_column_detail(col: dict[str, Any]) -> str:
    """渲染单列的"详情"信息(给 LLM 看,影响它写 spec 的决策)。"""
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
            # 全枚举(小基数列,LLM 可以用 op=eq / op=in 精确匹配)
            vals_str = ", ".join(str(v) for v in vals)
            parts.append(f"{cardinality} 个值:[{vals_str}]")
        elif "top_k" in col:
            top = col["top_k"][:10]
            top_str = ", ".join(str(v) for v in top)
            parts.append(
                f"{cardinality} 个值(高基数)。"
                f"top 10:[{top_str}]。**写 filter 优先用 op=icontains 或 op=all_tokens,不要 op=eq**"
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
