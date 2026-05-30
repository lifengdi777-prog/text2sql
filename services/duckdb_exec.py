"""DuckDB 执行层:让 DuckDB **直接读 parquet**

做法:每个 sheet 的 parquet 从对象存储下到一个临时文件,给 DuckDB 建一个
`read_parquet(本地文件)` 的视图;LLM 的 SQL 按视图名查,DuckDB 做列裁剪/谓词下推,
只解析用到的数据。查询结束删临时目录。

交给 DuckDB 直读 parquet 更省内存、更贴近列存查询引擎的用法。

安全:
  - 必须允许 DuckDB 读本地文件(默认即开)。为防 LLM 的 SQL 自己去读任意文件,
    validate_sql 里已禁掉 read_parquet/read_csv/glob 等读文件函数 —— LLM 只能用我们建好的视图;
  - 视图里的 read_parquet 路径由服务端用 schema 的 parquet_file 拼本地临时路径,不含用户输入。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any

import duckdb
import pandas as pd

from services import object_store
from services.dataset_loader import get_dataset_info

# 单次查询返回上限(防 LLM 写出无 LIMIT 的全表查询)
ROW_LIMIT = 1000


async def _materialize_sheets(dataset_id: int) -> tuple[dict[str, str], str]:
    """把数据集各 sheet 的 parquet 下到一个临时目录。返回 ({sheet: 本地路径}, tmpdir)。"""
    info = await get_dataset_info(dataset_id)
    if not info or not info.get("schema") or not info.get("folder_path"):
        raise ValueError(f"数据集 {dataset_id} schema/folder 不可用")
    folder = info["folder_path"]
    sheets = info["schema"].get("sheets") or {}

    tmpdir = tempfile.mkdtemp(prefix=f"wenshu_ds{dataset_id}_")
    paths: dict[str, str] = {}
    for i, (name, sinfo) in enumerate(sheets.items()):
        pq = sinfo.get("parquet_file")
        if not pq:
            continue
        raw = await asyncio.to_thread(object_store.get_bytes, f"{folder}/{pq}")
        local = os.path.join(tmpdir, f"sheet_{i}.parquet")
        with open(local, "wb") as f:
            f.write(raw)
        paths[name] = local
    if not paths:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise ValueError(f"数据集 {dataset_id} 没有可用的 parquet")
    return paths, tmpdir


def _new_con(paths: dict[str, str]) -> duckdb.DuckDBPyConnection:
    """内存连接,每个 sheet 建一个指向其 parquet 的视图。"""
    con = duckdb.connect(database=":memory:")
    for name, local in paths.items():
        view = name.replace('"', '""')
        safe_path = local.replace("\\", "/").replace("'", "''")
        con.execute(f'CREATE VIEW "{view}" AS SELECT * FROM read_parquet(\'{safe_path}\')')
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
    """绑定校验:语法 / 表名 / 列名错误会抛异常(EXPLAIN 不真正取数)。"""
    paths, tmpdir = await _materialize_sheets(dataset_id)

    def _run() -> None:
        con = _new_con(paths)
        try:
            con.execute(f"EXPLAIN {sql}")
        finally:
            con.close()

    try:
        await asyncio.to_thread(_run)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def query_sql(dataset_id: int, sql: str) -> list[dict[str, Any]]:
    """执行 SELECT,返回 rows(list[dict])。"""
    paths, tmpdir = await _materialize_sheets(dataset_id)

    def _run() -> pd.DataFrame:
        con = _new_con(paths)
        try:
            return con.execute(sql).fetch_df()
        finally:
            con.close()

    try:
        df = await asyncio.to_thread(_run)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return _df_to_rows(df)
