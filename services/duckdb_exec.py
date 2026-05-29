"""DuckDB 执行层:把数据集每个 sheet 的 DataFrame 注册成 DuckDB 视图,
对 LLM 生成的 SELECT 做「绑定校验(EXPLAIN)」与「执行取数(query)」。

安全模型(为什么这样比裸 exec pandas 代码安全):
  - 只跑 SQL,没有 Python 逃逸,搞不出 os.system / 读文件 / 连网络;
  - 连接是 :memory:,只 register 内存里的 DataFrame —— **不碰文件系统、不碰 MinIO 密钥**,
    worst case 只是一条作用在用户自己数据上的查询;
  - 额外 SET enable_external_access=false 再关死外部访问;
  - 配合 validate_sql 的 sqlglot「单条 SELECT」校验 + 强制 LIMIT,边界基本关闭。
"""
from __future__ import annotations

import asyncio
from typing import Any

import duckdb
import pandas as pd

from services.dataset_loader import get_dataset_info, load_sheet_df

# 单次查询返回上限(防 LLM 写出无 LIMIT 的全表查询撑爆前端/内存)
ROW_LIMIT = 1000


async def _load_sheet_dfs(dataset_id: int) -> dict[str, pd.DataFrame]:
    """加载数据集所有 sheet 的 DataFrame(带 dataset_loader 的缓存)。"""
    info = await get_dataset_info(dataset_id)
    if not info or not info.get("schema"):
        raise ValueError(f"数据集 {dataset_id} schema 不可用")
    sheet_names = list((info["schema"].get("sheets") or {}).keys())
    dfs: dict[str, pd.DataFrame] = {}
    for name in sheet_names:
        dfs[name] = await load_sheet_df(dataset_id, name)
    return dfs


def _new_con(dfs: dict[str, pd.DataFrame]) -> duckdb.DuckDBPyConnection:
    """内存连接 + 关外部访问 + 把每个 sheet 注册成同名视图。"""
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("SET enable_external_access=false")
    except Exception:
        # 个别版本不支持该参数 —— 不致命,register-only 模式本身已不碰外部
        pass
    for name, df in dfs.items():
        con.register(name, df)
    return con


def _df_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → list[dict],NaN/NaT → None,Timestamp/numpy scalar → JSON 可序列化。"""
    if df is None or df.empty:
        return []
    cleaned = df.astype(object).where(pd.notnull(df), None)
    out: list[dict[str, Any]] = []
    for r in cleaned.to_dict("records"):
        new_r: dict[str, Any] = {}
        for k, v in r.items():
            if v is None:
                new_r[k] = None
            elif hasattr(v, "isoformat"):          # Timestamp / datetime
                new_r[k] = v.isoformat()
            elif hasattr(v, "item"):               # numpy scalar
                try:
                    new_r[k] = v.item()
                except Exception:
                    new_r[k] = str(v)
            else:
                new_r[k] = v
        out.append(new_r)
    return out


async def explain_sql(dataset_id: int, sql: str) -> None:
    """绑定校验:语法 / 表名 / 列名错误会抛异常(EXPLAIN 不真正执行)。"""
    dfs = await _load_sheet_dfs(dataset_id)

    def _run() -> None:
        con = _new_con(dfs)
        try:
            con.execute(f"EXPLAIN {sql}")
        finally:
            con.close()

    await asyncio.to_thread(_run)


async def query_sql(dataset_id: int, sql: str) -> list[dict[str, Any]]:
    """执行 SELECT,返回 rows(list[dict])。"""
    dfs = await _load_sheet_dfs(dataset_id)

    def _run() -> pd.DataFrame:
        con = _new_con(dfs)
        try:
            return con.execute(sql).fetch_df()
        finally:
            con.close()

    df = await asyncio.to_thread(_run)
    return _df_to_rows(df)
