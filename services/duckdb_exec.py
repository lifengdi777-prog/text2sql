"""DuckDB 执行层:让 DuckDB **通过 httpfs 直读对象存储里的 parquet(s3://...)**,不落本地磁盘。

做法:给每个 sheet 建一个 `read_parquet('s3://bucket/folder/xxx.parquet')` 的视图;
LLM 的 SQL 按视图名查,DuckDB 用 HTTP range 请求**只取用到的列/行组**(列裁剪 + 谓词下推),
而不是把整份 parquet 拉到本地再读。

相比"下载到临时目录"的旧做法,这样:
  - 不落盘、无临时文件、无 cleanup;
  - 视图惰性求值 → 只有 SQL 真正引用到的 sheet 才会被读取(没用到的不发任何请求);
  - validate 的 EXPLAIN 只做绑定校验(读 parquet 的 footer/schema 元数据),不扫数据。

安全:
  - 连接开了 httpfs + S3 凭证,理论上能 read_parquet 任意 s3 路径;但 LLM 的 SQL 在
    dataset_agent/validate_sql 里已禁掉 read_parquet/read_csv/glob 等读文件函数 ——
    LLM 只能用我们建好的视图,视图里的 s3 路径由服务端用 schema 拼,不含用户输入。
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import duckdb
import pandas as pd

from conf.app_config import app_config
from services.dataset_loader import get_dataset_info

# 单次查询返回上限(防 LLM 写出无 LIMIT 的全表查询)
ROW_LIMIT = 1000

# httpfs 扩展只需在本进程装一次(INSTALL 会联网拉扩展;装过则缓存在 ~/.duckdb)。
# 之后每个连接 LOAD 即可。用进程级标志避免每次查询都触发 INSTALL 的检查。
_httpfs_installed = False


def _sql_str(v: str) -> str:
    """把字符串安全嵌进 DuckDB 的 SET / 字符串字面量(转义单引号)。"""
    return v.replace("'", "''")


def _configure_s3(con: duckdb.DuckDBPyConnection) -> None:
    """给连接装/载 httpfs 并配好指向对象存储(MinIO)的 S3 参数。"""
    global _httpfs_installed
    cfg = app_config.s3
    if cfg is None:
        raise RuntimeError("未配置 s3(对象存储),无法直读 parquet")

    if not _httpfs_installed:
        con.execute("INSTALL httpfs")
        _httpfs_installed = True
    con.execute("LOAD httpfs")

    parsed = urlparse(cfg.endpoint_url)          # http://localhost:9000
    host = parsed.netloc or parsed.path          # s3_endpoint 要 host:port,不含 scheme
    use_ssl = "true" if parsed.scheme == "https" else "false"

    con.execute(f"SET s3_region='{_sql_str(cfg.region)}'")
    con.execute(f"SET s3_endpoint='{_sql_str(host)}'")
    con.execute(f"SET s3_access_key_id='{_sql_str(cfg.access_key)}'")
    con.execute(f"SET s3_secret_access_key='{_sql_str(cfg.secret_key)}'")
    con.execute("SET s3_url_style='path'")        # MinIO / localhost 必须用 path-style
    con.execute(f"SET s3_use_ssl={use_ssl}")


async def _sheet_specs(dataset_id: int) -> tuple[str, str, list[tuple[str, str]]]:
    """取数据集的 (bucket, folder, [(sheet名, parquet文件名), ...])。"""
    info = await get_dataset_info(dataset_id)
    if not info or not info.get("schema") or not info.get("folder_path"):
        raise ValueError(f"数据集 {dataset_id} schema/folder 不可用")
    cfg = app_config.s3
    if cfg is None:
        raise RuntimeError("未配置 s3(对象存储),无法直读 parquet")
    folder = info["folder_path"]
    sheets = info["schema"].get("sheets") or {}
    specs = [
        (name, sinfo["parquet_file"])
        for name, sinfo in sheets.items()
        if sinfo.get("parquet_file")
    ]
    if not specs:
        raise ValueError(f"数据集 {dataset_id} 没有可用的 parquet")
    return cfg.bucket, folder, specs


def _connect(bucket: str, folder: str, specs: list[tuple[str, str]]) -> duckdb.DuckDBPyConnection:
    """内存连接 + httpfs/S3 配置 + 每个 sheet 一个指向 s3 parquet 的(惰性)视图。"""
    con = duckdb.connect(database=":memory:")
    _configure_s3(con)
    for name, pq in specs:
        view = name.replace('"', '""')
        s3_path = _sql_str(f"s3://{bucket}/{folder}/{pq}")
        con.execute(f'CREATE VIEW "{view}" AS SELECT * FROM read_parquet(\'{s3_path}\')')
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
    """绑定校验:语法 / 表名 / 列名错误会抛异常(EXPLAIN 只绑定、不取数)。"""
    bucket, folder, specs = await _sheet_specs(dataset_id)

    def _run() -> None:
        con = _connect(bucket, folder, specs)
        try:
            con.execute(f"EXPLAIN {sql}")
        finally:
            con.close()

    await asyncio.to_thread(_run)


async def query_sql(dataset_id: int, sql: str) -> list[dict[str, Any]]:
    """执行 SELECT,返回 rows(list[dict])。"""
    bucket, folder, specs = await _sheet_specs(dataset_id)

    def _run() -> pd.DataFrame:
        con = _connect(bucket, folder, specs)
        try:
            return con.execute(sql).fetch_df()
        finally:
            con.close()

    df = await asyncio.to_thread(_run)
    return _df_to_rows(df)
