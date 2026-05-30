"""上传时用 LLM 检测每个 sheet 的表头(处理标题行 / 空行 / 多行合并表头)。

流程:读前 N 行原始网格(header=None)→ LLM 返回每个 sheet 的
{data_start_row, columns}(列名按列顺序、已把多行表头扁平化)→ 校验合法后用于正式读取。
任何失败 / 不合法 → 该 sheet 不收进结果,调用方回退 header=0(= 旧行为,安全兜底)。
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from core.log import logger

_PREVIEW_ROWS = 15          # 喂给 LLM 的预览行数
_DETECT_TIMEOUT = 30        # 单次 LLM 超时(秒)
_DETECT_ATTEMPTS = 2        # 超时/失败重试次数(LLM 偶发卡顿,重来一次基本就好)
_DETECT_BACKOFF = 2.0       # 重试前的退避(秒):贴着重试多半还在限流窗口里,等一下再来
_PROMPT_REL = Path("agent/dataset_agent/prompts/detect_header.md")
_PROMPT_CACHE: str | None = None

# 表头检测专用的快模型客户端(独立于问数用的主 llm),懒加载单例。
# 用 app_config.llm.fast_model_name(默认 deepseek-v4-flash),复用同一 api_key / base_url。
_DETECT_LLM = None


def _get_detect_llm():
    global _DETECT_LLM
    if _DETECT_LLM is None:
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        from conf.app_config import app_config

        _DETECT_LLM = ChatOpenAI(
            model=app_config.llm.fast_model_name,
            api_key=SecretStr(app_config.llm.api_key),
            base_url=app_config.llm.base_url,
            temperature=0,
            timeout=_DETECT_TIMEOUT,
            max_retries=1,
        )
    return _DETECT_LLM


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_REL.read_text(encoding="utf-8")
    return _PROMPT_CACHE


class SheetHeader(BaseModel):
    sheet: str
    data_start_row: int          # 0-indexed,数据从第几行开始
    columns: list[str]           # 列名,顺序与列一致,数量 == 该 sheet 列数


class WorkbookHeaders(BaseModel):
    sheets: list[SheetHeader] = []


def _cell(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def read_sheet_previews(file_bytes: bytes) -> dict[str, list[list[str]]]:
    """每个 sheet 读前 _PREVIEW_ROWS 行的原始网格(header=None)。同步,放线程池跑。"""
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    out: dict[str, list[list[str]]] = {}
    for s in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=s, header=None, dtype=object, nrows=_PREVIEW_ROWS)
        out[s] = [[_cell(v) for v in row] for row in raw.itertuples(index=False, name=None)]
    return out


def _render_grid(grid: list[list[str]]) -> str:
    return "\n".join(
        f"[行{i}] " + " | ".join(c if c else "∅" for c in row)
        for i, row in enumerate(grid)
    )


def _grid_width(grid: list[list[str]]) -> int:
    return max((len(r) for r in grid), default=0)


def _valid(sh: SheetHeader, width: int) -> bool:
    return (
        width > 0
        and 0 <= sh.data_start_row <= _PREVIEW_ROWS
        and len(sh.columns) == width
        and all(c.strip() for c in sh.columns)
    )


async def detect_headers(previews: dict[str, list[list[str]]]) -> dict[str, SheetHeader]:
    """LLM 检测表头。返回 {sheet: SheetHeader};失败/不合法的 sheet 不在结果里(调用方回退 header=0)。"""
    if not previews:
        return {}

    from langchain.messages import HumanMessage, SystemMessage

    widths = {s: _grid_width(g) for s, g in previews.items()}
    blocks = [
        f"## Sheet「{s}」(共 {widths[s]} 列)\n{_render_grid(g)}"
        for s, g in previews.items()
    ]
    user = "\n\n".join(blocks) + "\n\n请按要求输出每个 sheet 的表头检测 JSON。"

    structured = _get_detect_llm().with_structured_output(WorkbookHeaders, method="json_mode")
    messages = [SystemMessage(content=_get_prompt()), HumanMessage(content=user)]

    result: WorkbookHeaders | None = None
    for attempt in range(1, _DETECT_ATTEMPTS + 1):
        try:
            result = await asyncio.wait_for(
                structured.ainvoke(messages),  # type: ignore
                timeout=_DETECT_TIMEOUT,
            )
            break
        except Exception as exc:
            # LLM 偶发卡顿/限流/超时 → 退避后重试;全失败才回退 header=0
            logger.warning(
                f"LLM 表头检测第 {attempt}/{_DETECT_ATTEMPTS} 次失败:{type(exc).__name__}: {exc}"
            )
            if attempt < _DETECT_ATTEMPTS:
                await asyncio.sleep(_DETECT_BACKOFF)
    if result is None:
        logger.warning("LLM 表头检测全部重试失败,全部回退 header=0")
        return {}

    out: dict[str, SheetHeader] = {}
    for sh in result.sheets:
        if sh.sheet in widths and _valid(sh, widths[sh.sheet]):
            out[sh.sheet] = sh
            logger.info(f"表头检测 sheet「{sh.sheet}」:data_start_row={sh.data_start_row} cols={sh.columns}")
        else:
            logger.warning(f"sheet「{sh.sheet}」表头检测结果不合法,回退 header=0")
    return out
