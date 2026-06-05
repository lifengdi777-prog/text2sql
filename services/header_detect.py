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


# ───────────────────────── 规则法检测(零依赖,LLM 兜底/交叉校验)─────────────────────────
def _is_number(s: str) -> bool:
    """单元格文本是否是个数字(容忍千分位 / 百分号)。空 → 否。"""
    if not s:
        return False
    t = s.replace(",", "").rstrip("%").strip()
    try:
        float(t)
        return True
    except ValueError:
        return False


def _ffill_row(row: list[str]) -> list[str]:
    """横向前向填充:处理合并单元格在右侧留空(如 [门店,销售,∅] → [门店,销售,销售])。"""
    out, last = [], ""
    for c in row:
        if c:
            last = c
        out.append(last)
    return out


def _flatten_header(header_rows: list[list[str]], width: int) -> list[str]:
    """把 1~N 行表头扁平化成 width 个列名:多行时横向前填充各行再按列上下拼接;空列用 列N 占位、重名加后缀。"""
    if len(header_rows) == 1:
        top = header_rows[0]
        names = [top[i] if i < len(top) and top[i] else "" for i in range(width)]
    else:
        filled = [_ffill_row(r + [""] * (width - len(r))) for r in header_rows]
        names = []
        for i in range(width):
            parts: list[str] = []
            for r in filled:
                v = r[i]
                if v and v not in parts:
                    parts.append(v)
            names.append("".join(parts))
    out, used = [], set()
    for i, n in enumerate(names):
        base = n.strip() if n.strip() else f"列{i + 1}"
        name, k = base, 1
        while name in used:
            k += 1
            name = f"{base}_{k}"
        used.add(name)
        out.append(name)
    return out


def detect_header_heuristic(grid: list[list[str]], width: int) -> SheetHeader | None:
    """规则法检测单个 sheet 的表头(零依赖)。判不准 → None(调用方交回 header=0)。

    思路:跳过开头"空行 / 单格标题行"(非空 ≤ 1)→ 第一个非空 ≥ 2 的行作表头候选;
    若候选行有**内部空缺**(疑似合并表头)且下一行"全是文字(无数字)",再并一行一起扁平化;
    候选(组)之后即数据起点。
    """
    if width <= 0 or not grid:
        return None
    # 1) 跳过标题/空行,定位表头候选行(第一处非空单元格 ≥ 2)
    start = next((i for i, row in enumerate(grid) if sum(1 for c in row if c) >= 2), None)
    if start is None:
        return None
    head = grid[start] + [""] * (width - len(grid[start]))
    header_rows = [head]
    data_start = start + 1
    # 2) 合并表头:候选行最后一个非空之前还有空缺(横向合并痕迹)+ 下一行全文字 → 再并一行(上限 2 行)
    last_filled = max((i for i in range(width) if head[i]), default=-1)
    has_internal_gap = any(not head[i] for i in range(last_filled))
    if has_internal_gap and data_start < len(grid):
        nxt = grid[data_start] + [""] * (width - len(grid[data_start]))
        if any(nxt) and not any(_is_number(c) for c in nxt):
            header_rows.append(nxt)
            data_start += 1
    # 3) 扁平化 + 复用 LLM 路径的合法性校验
    sh = SheetHeader(sheet="", data_start_row=data_start, columns=_flatten_header(header_rows, width))
    return sh if _valid(sh, width) else None


def detect_headers_heuristic(previews: dict[str, list[list[str]]]) -> dict[str, SheetHeader]:
    """对每个 sheet 跑规则法检测。返回 {sheet: SheetHeader};判不准的 sheet 不在结果里。"""
    out: dict[str, SheetHeader] = {}
    for s, grid in previews.items():
        sh = detect_header_heuristic(grid, _grid_width(grid))
        if sh is not None:
            sh.sheet = s
            out[s] = sh
    return out


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
