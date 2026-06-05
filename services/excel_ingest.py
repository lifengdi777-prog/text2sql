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
import warnings
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clients.es import es_client
from conf.app_config import app_config
from core.log import logger
from repositories.es import UploadESRepository
from repositories.upload import UploadDatasetRepository
from services import object_store
from services.header_detect import SheetHeader, detect_headers, read_sheet_previews

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

def parse_workbook(
    file_bytes: bytes,
    headers: dict[str, SheetHeader] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """读 Excel 所有 sheet。

    若传入 headers(LLM 检测结果):跳过表头上方的标题/空行,从 data_start_row 起读数据,
    并用检测到的列名;列数对不上或没检测结果 → 回退 header=0(旧行为,安全兜底)。

    返回 (out, starts):
      out[sheet]    = DataFrame
      starts[sheet] = data_start_excel_row —— pandas index 0 对应的 **1-based Excel 行号**,
                      供 clean_sheet 把每条数据行映射回原始 Excel 行(智能助手保样式回写用)。
                      header 检测路径 = data_start_row(0-based)+ 1;header=0 兜底 = 2。
    """
    headers = headers or {}
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    out: dict[str, pd.DataFrame] = {}
    starts: dict[str, int] = {}
    for s in xls.sheet_names:
        spec = headers.get(s)
        if spec is not None:
            df = pd.read_excel(xls, sheet_name=s, header=None, dtype=object,
                               skiprows=spec.data_start_row)
            if df.shape[1] == len(spec.columns):
                df.columns = spec.columns
                out[s] = df
                starts[s] = spec.data_start_row + 1
                continue
            logger.warning(
                f"sheet「{s}」列数({df.shape[1]})与检测列名({len(spec.columns)})不符,回退 header=0"
            )
        out[s] = pd.read_excel(xls, sheet_name=s, header=0, dtype=object)
        starts[s] = 2  # header 在 Excel 第 1 行,数据从第 2 行起
    return out, starts


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


def _total_row_mask(df: pd.DataFrame) -> pd.Series:
    """标出 小计/合计/总计 行:任一单元格精确等于关键词即判为汇总行。返回布尔 Series。

    问数会丢弃这些行(避免聚合重复计算);智能助手则把它们加回可编辑表(见 clean_sheet)。
    """
    def is_total(row: pd.Series) -> bool:
        for v in row:
            if isinstance(v, str) and v.strip().lower() in _TOTAL_KEYWORDS:
                return True
        return False
    return df.apply(is_total, axis=1)


# 日期特征:含 2025-01 / 2025/1 / 2025年 / 时:分 之类才值得尝试日期解析
_DATE_HINT_RE = r"\d{4}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}年|\d{1,2}:\d{2}"


def _looks_date_like(stripped: pd.Series) -> bool:
    """抽样判断这列像不像日期 —— 避免对纯文本列(品类/商品名)跑昂贵的逐元素 dateutil。"""
    sample = stripped.head(20)
    if sample.empty:
        return False
    return sample.str.contains(_DATE_HINT_RE, regex=True, na=False).mean() >= 0.5


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

    # 只对"看起来像日期"的列尝试解析:纯文本列直接跳过,既不刷 warning 也不跑慢速 dateutil
    if _looks_date_like(stripped):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # 抑制 "Could not infer format" 噪音
            as_dt = pd.to_datetime(stripped, errors="coerce")
            if as_dt.notna().mean() >= 0.8:
                return pd.to_datetime(s, errors="coerce")

    # 否则按字符串列处理:**统一转成 str(NaN→None)**,并强制回 object dtype。
    # · 字符串化:object 列若混入 int/float(表头识别失败、表头行被当数据)会让 to_parquet 崩
    #   (pyarrow 不接受一列 str+int 混排),统一字符串保证一定能写 parquet;
    # · astype(object):pandas 会把全字符串列推断成 StringDtype,而下游(ES 值索引等)
    #   用 `dtype == object` 识别字符串列,这里固定回 object 保持一致。
    cleaned = s.map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return cleaned.astype(object)


def clean_sheet(
    df: pd.DataFrame,
    data_start_excel_row: int = 2,
) -> tuple[pd.DataFrame | None, dict | None]:
    """单 sheet 清洗 + 捕获血缘(供「智能助手」保样式回写原件)。

    清洗:删空行列 → 列名规整 → 删合计行 → 类型清洗(行为与原版一致)。
    血缘:记录每条 canonical 行/列与原始 Excel 坐标的映射 —— 这是导出 diff-patch
    能把变更写回原件正确单元格的前提(详见 docs/dataset_edit_agent_design.md §2.3/§6)。

    返回 (cleaned_df, lineage);空表返回 (None, None)。
    lineage = {
        header_row, data_start_row,   # 1-based Excel 行
        row_origin: [每条 canonical 行的原始 Excel 行号(1-based)],
        col_excel:  {canonical 列名: 原始 Excel 列号(1-based)},
    }
    data_start_excel_row: pandas index 0 对应的 1-based Excel 行(parse_workbook 给出)。
    """
    # 1) 删全空行(保留 index 标签 = 原始 0-based 读入位置)
    df = df.dropna(axis=0, how="all")
    if df.empty:
        return None, None
    # 2) 删全空列 —— 用 positional 布尔掩码替代 dropna(axis=1,how='all'):等价,但能
    #    精确记录"存活列 → 原始 Excel 列号",且对重复列名也安全(不靠 label)
    keep_mask = df.notna().any(axis=0).to_numpy()
    surviving_excel_cols = [i + 1 for i, keep in enumerate(keep_mask) if keep]
    df = df.loc[:, keep_mask]
    if df.empty:
        return None, None
    # 3) 列名规整(只重命名,不改顺序/数量)→ 建 列名→Excel列号 映射
    df.columns = _normalize_columns(list(df.columns))
    col_excel = {name: surviving_excel_cols[k] for k, name in enumerate(df.columns)}
    # 4) 标出合计/汇总行(剔除前按字符串匹配),但**先不删**
    total_mask = _total_row_mask(df)
    # 5) 类型清洗:对**含合计行的全表**做,保证合计行的数值列(如 "48,080")也被解析成数字,
    #    这样后面把合计行加回编辑表时列类型与明细一致(否则混入文本会让整列退化成 VARCHAR)。
    for c in df.columns:
        df[c] = _coerce_column(df[c])
    # 6) 拆分:kept = 明细(进 parquet,问数只看它);dropped = 合计行(只记进血缘给编辑加回)
    kept = df[~total_mask]
    if kept.empty:
        return None, None
    dropped = df[total_mask]
    # 合计行:记下原始 Excel 行号 + 各列值(供 duckdb_edit 物化时加回可编辑表)。
    # 问数 parquet 仍只含 kept,完全不受影响。
    extra_rows = [
        {
            "excel_row": int(idx) + data_start_excel_row,
            "values": {col: _jsonable(dropped.at[idx, col]) for col in dropped.columns},
        }
        for idx in dropped.index
    ]
    # reset 前捕获明细行来源:index 标签是原始 0-based 读入位置 → + offset 得 1-based Excel 行
    row_origin = [int(p) + data_start_excel_row for p in kept.index]
    kept = kept.reset_index(drop=True)
    lineage = {
        "header_row": data_start_excel_row - 1,
        "data_start_row": data_start_excel_row,
        "row_origin": row_origin,
        "col_excel": col_excel,
        # 被剔除的合计/汇总行(问数不读;编辑物化时加回,使其可见可改)
        "extra_rows": extra_rows,
    }
    return kept, lineage


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


# ───────── 表头错位检测(兜底用)──────────────────────────

_PLACEHOLDER_COL_RE = re.compile(r"^列\d+$")


def _looks_like_misread_header(df: pd.DataFrame) -> bool:
    """判断一个 sheet 的表头是不是"没对齐"(真表头没在第一行 → 列名大多是 列N 占位)。

    出现在:LLM 表头识别失败/超时 → 回退 header=0 套在脏表上时。
    判据:占位列名(列N / Unnamed)≥2 个 且 占比 ≥ 50%。
    """
    cols = [str(c) for c in df.columns]
    if not cols:
        return False
    placeholders = sum(
        1 for c in cols if _PLACEHOLDER_COL_RE.match(c) or c.startswith("Unnamed")
    )
    return placeholders >= 2 and placeholders / len(cols) >= 0.5


# ───────── 主编排 ──────────────────────────────────────

async def ingest_excel(user_id: str, filename: str, file_bytes: bytes) -> dict[str, Any]:
    """上传主入口(**非阻塞**):查重 → 立刻建一行(status=cleaning)拿 dataset_id → 秒返回;
    重活(AI 表头识别 / 清洗 / parquet / schema / ES 索引)全部丢后台,完成后状态推到 ready/failed。

    这样上传请求秒回,前端卡片立刻出现并显示「处理中」,用户可继续操作,无需等 AI 解析完。
    """
    Session = get_session_factory()

    # 0. 算 hash + 查重(毫秒级)
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        existing = await repo.find_existing(user_id, filename, content_hash)
    if existing is not None:
        logger.info(f"上传去重命中:user={user_id} file={filename} → 复用 dataset_id={existing.id}")
        return {
            "dataset_id": existing.id, "name": existing.name, "status": existing.status,
            "sheet_count": existing.sheet_count, "total_rows": existing.total_rows,
            "duplicated": True,
        }

    # 1. 立刻建一行(status=cleaning)拿 dataset_id —— 卡片马上能显示「处理中」
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        ds = await repo.create(user_id=user_id, name=filename, original_filename=filename,
                               status="cleaning", content_hash=content_hash)
        dataset_id = ds.id
        await session.commit()

    # 2. 重活丢后台,立即返回(前端靠轮询 status 等卡片变 ready/failed)
    asyncio.create_task(_process_dataset(dataset_id, filename, file_bytes))

    return {
        "dataset_id": dataset_id, "name": filename, "status": "cleaning",
        "sheet_count": 0, "total_rows": 0, "duplicated": False,
    }


async def _process_dataset(dataset_id: int, filename: str, file_bytes: bytes) -> None:
    """后台处理:AI 表头识别 → 清洗 → parquet → schema → finalize(indexing)→ ES → ready。
    任一步失败 → status=failed + 错误信息(供卡片提示)。
    """
    Session = get_session_factory()
    try:
        # AI 表头识别(失败/超时回退 header=0)
        previews = await asyncio.to_thread(read_sheet_previews, file_bytes)
        header_specs = await detect_headers(previews)

        # 解析 + 清洗(同时捕获血缘)
        def _parse_and_clean() -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
            raw, starts = parse_workbook(file_bytes, header_specs)
            cleaned: dict[str, pd.DataFrame] = {}
            lineages: dict[str, dict] = {}
            for name, df in raw.items():
                c, lin = clean_sheet(df, starts.get(name, 2))
                if c is not None and not c.empty:
                    cleaned[name] = c
                    lineages[name] = lin
            return cleaned, lineages

        cleaned_sheets, sheet_lineages = await asyncio.to_thread(_parse_and_clean)
        if not cleaned_sheets:
            raise ValueError("文件中没有解析出任何有效数据(可能是空表或格式不支持)")

        # 表头错位兜底:回退 header=0 后若列大多是占位名,说明真表头没在第一行 → 明确失败
        misread = [name for name, df in cleaned_sheets.items() if _looks_like_misread_header(df)]
        if misread:
            raise ValueError(
                f"表头识别异常,以下表的表头似乎没对齐(疑似有标题行/合并表头):{'、'.join(misread)}。"
                f"请重试上传,或把表头整理到第一行后重传。"
            )

        # 写 parquet + 原始 Excel 留档
        prefix = f"ds_{dataset_id}"
        sheet_files = await asyncio.to_thread(save_parquets, prefix, cleaned_sheets)
        original_key = f"{prefix}/original/{_safe_filename(filename, 'upload.xlsx')}"
        await asyncio.to_thread(
            object_store.put_bytes, original_key, file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # profile schema
        def _build_schema() -> tuple[dict, int]:
            schema = {"sheets": {}}
            total = 0
            for name, df in cleaned_sheets.items():
                row_count = int(len(df))
                schema["sheets"][name] = {
                    "row_count": row_count,
                    "parquet_file": sheet_files[name],
                    "columns": profile_columns(df),
                    # 血缘:智能助手保样式回写原件用;问数不读它,纯增量字段
                    "lineage": sheet_lineages.get(name),
                }
                total += len(df)
            return schema, total

        schema_json, total_rows = await asyncio.to_thread(_build_schema)

        # finalize → status=indexing
        async with Session() as session:
            repo = UploadDatasetRepository(session)
            await repo.finalize(dataset_id=dataset_id, folder_path=prefix,
                                schema_json=schema_json, sheet_count=len(cleaned_sheets),
                                total_rows=total_rows)
            await session.commit()
        logger.info(f"数据集 {dataset_id}({filename})解析完成:{len(cleaned_sheets)} sheet,{total_rows} 行")

        # ES 值索引 → status=ready(沿用现有后台函数,内部 finally 会置 ready)
        await build_es_index_background(dataset_id, cleaned_sheets)

    except Exception as exc:
        logger.exception(f"数据集 {dataset_id} 后台处理失败:{exc}")
        try:
            async with Session() as session:
                repo = UploadDatasetRepository(session)
                await repo.mark_failed(dataset_id, str(exc))
                await session.commit()
        except Exception:
            logger.exception(f"数据集 {dataset_id} 标记 failed 也失败")


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

    # MySQL 行 + 连带清理「智能助手」编辑会话/操作日志(编辑结果不回写源,删源即全清)
    async with Session() as session:
        from repositories.dataset_edit import DatasetEditRepository
        await DatasetEditRepository(session).delete_by_dataset(dataset_id)
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
