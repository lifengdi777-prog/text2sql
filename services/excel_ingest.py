"""Excel 上传 → 清洗 → 入库 全流程。

数据流:
  bytes
    ↓ parse(pandas read_excel,sync 走线程池)
  raw {sheet: DataFrame}
    ↓ clean(去空行列/合计行/列名规整/类型清洗)
  cleaned {sheet: DataFrame}
    ↓ 并行:
    ├─ 写 parquet 到对象存储 ds_{id}/{sheet}.parquet(MinIO/S3)
    ├─ 留档原始 Excel 到 ds_{id}/original/{filename}
    ├─ profile_columns:算每列 dtype/cardinality/values/top_K/min/max
    └─ 写 MySQL upload_datasets(schema_json 列,folder_path=对象前缀 ds_{id})
    ↓ 异步后台
    └─ 提取 distinct 值灌 ES upload_value_info(为查询时的值召回准备)
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clients.es import es_client
from conf.app_config import app_config
from core.log import logger
from repositories.es import UploadESRepository
from repositories.upload import UploadDatasetRepository
from services import object_store

# ───────── 常量 ─────────────────────────────────────────
_TOTAL_KEYWORDS = {"小计", "合计", "总计", "汇总", "total", "subtotal", "sum"}
_SMALL_CARD_THRESHOLD = 50         # 小基数列阈值,小于此则全枚举
_TOP_K = 5                          # 高基数「分类」列保留的样例值个数(让 LLM 知道值长什么样)
_ES_VALUE_LIMIT_PER_COL = 10000     # 单列最多入 ES 的 distinct 值
_TEMPORAL_NAME_HINTS = ("date", "year", "month", "quarter", "day", "week", "time",
                        "时间", "日期", "年", "月", "日", "季度", "周")
_ID_NAME_SUFFIXES = ("_id", "编号", "代码", "号")


# ───────── 全局 sessionmaker(lazy 单例)──────────────────
_session_factory: async_sessionmaker | None = None
_engine = None


def get_session_factory() -> async_sessionmaker:
    """惰性构造 upload 库的 sessionmaker(进程级单例,自带连接池)。"""
    global _session_factory, _engine
    if _session_factory is None:
        cfg = app_config.db_upload
        if cfg is None:
            raise RuntimeError("未配置 db_upload,请在 app_config.yaml 添加")
        uri = (f"mysql+asyncmy://{cfg.user}:{cfg.password}@"
               f"{cfg.host}:{cfg.port}/{cfg.database}?charset=utf8mb4")
        _engine = create_async_engine(uri, pool_size=5, max_overflow=10, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory


# ───────── 清洗 ─────────────────────────────────────────

def parse_workbook(file_bytes: bytes) -> dict[str, pd.DataFrame]:
    """读 Excel 所有 sheet,header=0 默认表头在第 1 行(MVP 简单做法)。"""
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    return {s: pd.read_excel(xls, sheet_name=s, header=0, dtype=object)
            for s in xls.sheet_names}


def _normalize_columns(cols: list[Any]) -> list[str]:
    """列名清理:Unnamed/空 → 列N;重名加后缀。"""
    out, used = [], set()
    for i, c in enumerate(cols):
        if c is None or (isinstance(c, float) and pd.isna(c)) \
                or str(c).startswith("Unnamed") or str(c).strip() == "":
            base = f"列{i + 1}"
        else:
            base = str(c).strip()
        name, k = base, 1
        while name in used:
            k += 1
            name = f"{base}_{k}"
        used.add(name)
        out.append(name)
    return out


def _drop_total_rows(df: pd.DataFrame) -> pd.DataFrame:
    """删 小计/合计/总计 行:任一单元格精确等于关键词就删。"""
    def is_total(row: pd.Series) -> bool:
        for v in row:
            if isinstance(v, str) and v.strip().lower() in _TOTAL_KEYWORDS:
                return True
        return False
    return df[~df.apply(is_total, axis=1)]


def _coerce_column(s: pd.Series) -> pd.Series:
    """对 object 列:去千分位/货币/百分号 → 试数值 → 试日期 → 否则保留字符串。"""
    if s.dtype != object:
        return s
    stripped = s.dropna().astype(str)
    if stripped.empty:
        return s

    cleaned = stripped.str.replace(r"[,¥$\s元]", "", regex=True).str.replace("%", "", regex=False)
    as_num = pd.to_numeric(cleaned, errors="coerce")
    if as_num.notna().mean() >= 0.8:
        full = s.astype(str).str.replace(r"[,¥$\s元]", "", regex=True).str.replace("%", "", regex=False)
        return pd.to_numeric(full, errors="coerce")

    as_dt = pd.to_datetime(stripped, errors="coerce")
    if as_dt.notna().mean() >= 0.8:
        return pd.to_datetime(s, errors="coerce")

    return s


def clean_sheet(df: pd.DataFrame) -> pd.DataFrame | None:
    """单 sheet 清洗:删空行列 → 列名规整 → 删合计行 → 类型清洗。"""
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if df.empty:
        return None
    df.columns = _normalize_columns(list(df.columns))
    df = _drop_total_rows(df)
    if df.empty:
        return None
    for c in df.columns:
        df[c] = _coerce_column(df[c])
    return df.reset_index(drop=True)


# ───────── Profile ─────────────────────────────────────

def _infer_semantic_type(col_name: str, series: pd.Series) -> str:
    """temporal / categorical / numeric。

    **dtype 权威优先**:pandas 清洗后已解析出真实类型,先按 dtype 判,
    避免列名误判(如「年龄」含「年」被当成时间)。只有 object/字符串列才用列名 hint 辅助。
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return "temporal"
    if pd.api.types.is_bool_dtype(series):
        return "categorical"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    # 到这里是 object/字符串列:用列名关键词辅助(date/月份/编号 等)
    name_low = str(col_name).lower()
    if any(h in name_low for h in _TEMPORAL_NAME_HINTS):
        return "temporal"
    if any(name_low.endswith(s) for s in _ID_NAME_SUFFIXES):
        return "categorical"
    return "categorical"


def _jsonable(v: Any) -> Any:
    """把单元格值转成 JSON 可序列化的形式。

    注意 numpy 2.x 中 np.int64 不再是 Python int 的子类,所以要走 .item()
    把 numpy scalar 转成 Python 原生类型,否则会被 str() 误吞。
    """
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    # numpy scalar(np.int64 / np.float64 等)→ Python 原生类型
    if hasattr(v, "item"):
        try:
            return v.item()
        except (ValueError, TypeError):
            pass
    return str(v)


def profile_columns(df: pd.DataFrame) -> list[dict[str, Any]]:
    """算每列元信息,塞进 schema_json。"""
    profiles: list[dict[str, Any]] = []
    for col in df.columns:
        s = df[col]
        semantic_type = _infer_semantic_type(col, s)
        distinct = s.dropna().unique()
        cardinality = int(len(distinct))
        null_count = int(s.isna().sum())

        p: dict[str, Any] = {
            "name": col,
            "dtype": str(s.dtype),
            "semantic_type": semantic_type,
            "cardinality": cardinality,
            "null_count": null_count,
        }

        # 小基数 categorical/temporal 全枚举(数值列不全枚举,用范围)
        if semantic_type != "numeric" and cardinality <= _SMALL_CARD_THRESHOLD:
            try:
                vals_sorted = sorted(distinct.tolist(), key=lambda x: str(x))
            except Exception:
                vals_sorted = list(distinct)
            p["values"] = [_jsonable(v) for v in vals_sorted]
            p["is_high_cardinality"] = False
        elif semantic_type == "categorical":
            # 只有「高基数分类列」才存 top_k —— 让 LLM 知道值长什么样(写 ILIKE 模糊匹配)。
            # 时间/数值列靠下面的 min/max(/mean)范围即可,不存 top_k(纯浪费,prompt 也不渲染)。
            top = s.value_counts(dropna=True).head(_TOP_K).index.tolist()
            p["top_k"] = [_jsonable(v) for v in top]
            p["is_high_cardinality"] = True
        else:
            # 高基数的 temporal / numeric:不存样例值,只标记(数值不算高基数)
            p["is_high_cardinality"] = False

        # 数值/时间列加范围
        if semantic_type == "numeric":
            non_null = s.dropna()
            if len(non_null) > 0:
                p["min"] = _jsonable(non_null.min())
                p["max"] = _jsonable(non_null.max())
                try:
                    p["mean"] = round(float(non_null.mean()), 2)
                except Exception:
                    pass
        elif semantic_type == "temporal":
            non_null = s.dropna()
            if len(non_null) > 0:
                p["min"] = str(non_null.min())
                p["max"] = str(non_null.max())

        profiles.append(p)
    return profiles


# ───────── 文件命名 ────────────────────────────────────

def _safe_filename(name: str, fallback: str) -> str:
    """sheet 名 → 安全文件名:去掉 / \\ : * ? " < > | 之类。"""
    s = re.sub(r'[\\/:*?"<>|]', "_", str(name).strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return (s or fallback)[:100]


# ───────── 写 parquet ──────────────────────────────────

def save_parquets(prefix: str, sheets: dict[str, pd.DataFrame]) -> dict[str, str]:
    """每 sheet 一个 parquet,上传到对象存储 {prefix}/{name}.parquet。

    返回 {sheet_name → parquet 文件名(不含前缀)}。文件名存进 schema_json,
    folder_path 存 prefix,读取时拼成完整 object key。
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    for i, (sheet_name, df) in enumerate(sheets.items()):
        safe = _safe_filename(sheet_name, f"sheet_{i + 1}")
        # 防止 sheet 名 sanitize 后撞名
        name = safe
        k = 1
        while name in used:
            k += 1
            name = f"{safe}_{k}"
        used.add(name)
        filename = f"{name}.parquet"
        object_store.upload_df_parquet(f"{prefix}/{filename}", df)
        out[sheet_name] = filename
    return out


# ───────── ES 值索引(后台 task)─────────────────────────

# 标识/编号类列名:这类即使是高基数文本也不进 ES(自然语言查询极少出现原始 ID,纯噪音)
_ES_ID_LIKE_SUFFIXES = ("_id", "id", "编号", "代码", "号", "code", "key")


def _is_id_like_col(name: str) -> bool:
    nl = str(name).lower()
    return any(nl.endswith(s) for s in _ES_ID_LIKE_SUFFIXES)


def _extract_distinct_values(sheets: dict[str, pd.DataFrame]) -> list[dict]:
    """提取「高基数文本列」的 distinct 值灌 ES,供查询时的值召回。

    只索引高基数自然文本列(商品名/客户姓名/地址…),因为:
      - 小基数文本列(≤ _SMALL_CARD_THRESHOLD)已在 schema 里全枚举,LLM 直接能精确匹配,无需 ES;
      - 标识/编号列(客户ID/订单号)自然语言查询用不上,纯噪音,跳过;
      - 数值/日期列本就不进 ES(靠 min/max 范围)。
    """
    docs: list[dict] = []
    for sheet_name, df in sheets.items():
        for col in df.columns:
            # 只看字符串列(数值/日期不进 ES)
            if df[col].dtype != object:
                continue
            # 跳过标识/编号类列
            if _is_id_like_col(col):
                continue
            distinct = df[col].dropna().astype(str).unique()
            # 小基数列已在 schema 全枚举,不必再进 ES
            if len(distinct) <= _SMALL_CARD_THRESHOLD:
                continue
            if len(distinct) > _ES_VALUE_LIMIT_PER_COL:
                logger.warning(
                    f"sheet={sheet_name} col={col} 有 {len(distinct)} 个 distinct 值,"
                    f"截断为前 {_ES_VALUE_LIMIT_PER_COL}"
                )
                distinct = distinct[:_ES_VALUE_LIMIT_PER_COL]
            for v in distinct:
                docs.append({"sheet": sheet_name, "col": col, "value": str(v)})
    return docs


async def _mark_dataset_ready(dataset_id: int) -> None:
    """把数据集从 indexing 置为 ready。失败只 log——状态更新失败不该影响数据可用性。"""
    try:
        Session = get_session_factory()
        async with Session() as session:
            repo = UploadDatasetRepository(session)
            await repo.update_status(dataset_id, "ready")
            await session.commit()
    except Exception as exc:
        logger.exception(f"数据集 {dataset_id} 置 ready 失败:{exc}")


async def build_es_index_background(dataset_id: int, sheets: dict[str, pd.DataFrame]) -> None:
    """后台任务:把 distinct 值灌进 ES,结束后把数据集置为 ready。

    ES 是「锦上添花」的值召回增强,即使建索引失败,parquet 数据本身也能正常问数,
    所以无论成功/无值/失败,最后都把状态推进到 ready(否则卡片会永远停在「索引创建中」)。
    """
    try:
        docs = _extract_distinct_values(sheets)
        if not docs:
            logger.info(f"数据集 {dataset_id} 无字符串列,跳过 ES 索引")
            return
        repo = UploadESRepository(es_client.client)
        await repo.index_dataset_values(dataset_id, docs)
        logger.info(f"数据集 {dataset_id} ES 索引完成({len(docs)} 条值)")
    except Exception as exc:
        logger.exception(f"数据集 {dataset_id} ES 索引失败(不影响主流程):{exc}")
    finally:
        await _mark_dataset_ready(dataset_id)


# ───────── 主编排 ──────────────────────────────────────

async def ingest_excel(user_id: str, filename: str, file_bytes: bytes) -> dict[str, Any]:
    """主入口:Excel → 清洗 → 入库 → 后台建 ES 索引。返回数据集摘要。

    去重:同 user_id + 同文件名 + 同 SHA-256 → 直接返回已有 dataset_id,跳过处理。
    """
    Session = get_session_factory()

    # 0. 算 hash + 查重(纯 IO + 一次 MySQL 索引查询,毫秒级)
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        existing = await repo.find_existing(user_id, filename, content_hash)
    if existing is not None:
        logger.info(f"上传去重命中:user={user_id} file={filename} → 复用 dataset_id={existing.id}")
        return {
            "dataset_id": existing.id,
            "name": existing.name,
            "folder_path": existing.folder_path,
            "sheet_count": existing.sheet_count,
            "total_rows": existing.total_rows,
            "duplicated": True,                # ← 告诉前端这次是去重命中
        }

    # 1. parse + clean(纯 pandas,放线程池跑)
    def _parse_and_clean() -> dict[str, pd.DataFrame]:
        raw = parse_workbook(file_bytes)
        cleaned: dict[str, pd.DataFrame] = {}
        for name, df in raw.items():
            c = clean_sheet(df)
            if c is not None and not c.empty:
                cleaned[name] = c
        return cleaned

    cleaned_sheets = await asyncio.to_thread(_parse_and_clean)
    if not cleaned_sheets:
        raise ValueError("文件中没有解析出任何有效数据(可能是空表或格式不支持)")

    # 2. 先插一行 MySQL 拿 dataset_id(带 hash,方便未来再查重)
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        ds = await repo.create(user_id=user_id, name=filename, original_filename=filename,
                               status="cleaning", content_hash=content_hash)
        dataset_id = ds.id
        await session.commit()

    try:
        # 3. 写 parquet 到对象存储(folder_path 存对象前缀,不再是本地路径)
        prefix = f"ds_{dataset_id}"
        sheet_files = await asyncio.to_thread(save_parquets, prefix, cleaned_sheets)

        # 3b. 原始 Excel 留档(供下载/重新处理),放 {prefix}/original/{文件名}
        original_key = f"{prefix}/original/{_safe_filename(filename, 'upload.xlsx')}"
        await asyncio.to_thread(
            object_store.put_bytes,
            original_key,
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # 4. profile 每个 sheet
        def _build_schema() -> tuple[dict, int]:
            schema = {"sheets": {}}
            total = 0
            for name, df in cleaned_sheets.items():
                row_count = int(len(df))
                profile = profile_columns(df)
                schema["sheets"][name] = {
                    "row_count": row_count,
                    "parquet_file": sheet_files[name],
                    "columns": profile,
                }
                total += len(df)
            return schema, total

        schema_json, total_rows = await asyncio.to_thread(_build_schema)

        # 5. finalize MySQL 行(填 folder_path + schema + status=ready)
        async with Session() as session:
            repo = UploadDatasetRepository(session)
            await repo.finalize(
                dataset_id=dataset_id,
                folder_path=prefix,
                schema_json=schema_json,
                sheet_count=len(cleaned_sheets),
                total_rows=total_rows,
            )
            await session.commit()

        # 6. 后台异步建 ES 值索引(不阻塞返回)
        asyncio.create_task(build_es_index_background(dataset_id, cleaned_sheets))

        logger.info(f"数据集 {dataset_id}({filename})入库完成:"
                    f"{len(cleaned_sheets)} sheet,{total_rows} 行")
        return {
            "dataset_id": dataset_id,
            "name": filename,
            "folder_path": prefix,
            "sheet_count": len(cleaned_sheets),
            "total_rows": total_rows,
            "duplicated": False,
            "sheets": [
                {
                    "sheet": name,
                    "row_count": int(len(df)),
                    "columns": list(df.columns),
                }
                for name, df in cleaned_sheets.items()
            ],
        }

    except Exception as exc:
        logger.exception(f"数据集 {dataset_id} 入库失败:{exc}")
        # 出错时标 failed,已上传对象保留供 debug,catalog 行不删
        async with Session() as session:
            repo = UploadDatasetRepository(session)
            await repo.update_status(dataset_id, "failed")
            await session.commit()
        raise


# ───────── 删除 ────────────────────────────────────────

async def delete_dataset(dataset_id: int) -> bool:
    """删数据集:ES → 对象存储 → MySQL 行,三处同步清。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        ds = await repo.get(dataset_id)
        if ds is None:
            return False
        prefix = ds.folder_path

    # ES:删该 dataset 的所有值文档(失败只 warn,不阻断)
    try:
        es_repo = UploadESRepository(es_client.client)
        await es_repo.delete_dataset_values(dataset_id)
    except Exception as exc:
        logger.warning(f"ES 清理 dataset {dataset_id} 失败:{exc}")

    # 对象存储:删该 dataset 前缀下所有对象(原始 Excel + 各 parquet)
    if prefix:
        try:
            await asyncio.to_thread(object_store.delete_prefix, prefix)
        except Exception as exc:
            logger.warning(f"对象存储清理 dataset {dataset_id} 失败:{exc}")

    # MySQL 行
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        await repo.delete(dataset_id)
        await session.commit()

    # 清进程内缓存(避免下次查询拿到陈旧数据)。延迟导入避免循环依赖。
    try:
        from services.dataset_loader import invalidate_cache
        invalidate_cache(dataset_id)
    except Exception:
        pass

    logger.info(f"数据集 {dataset_id} 已删除")
    return True
