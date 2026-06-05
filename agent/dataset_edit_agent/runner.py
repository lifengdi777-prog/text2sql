"""「智能助手」编辑运行器:把一句自然语言指令变成对副本的一次编辑。

线性编排(无 fan-out,故不用 LangGraph 子图):
  生成 SQL(LLM)→ 静态校验(edit_sql_guard)⇄ 修正(LLM,≤MAX_RETRY)
  → 试执行于一次性副本(= 绑定校验 + 算 diff)
  → 破坏性且未确认 → 发"待确认"卡并停;否则 → 落 op 日志 + 发预览/diff

对外产出 WSStepInfo 流(与问数同协议,前端复用渲染)。DuckDB/openpyxl/S3 等阻塞操作
统一用 asyncio.to_thread 包,避免堵塞事件循环。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger
from repositories.dataset_edit import DatasetEditRepository
from services.dataset_loader import get_dataset_info
from services.duckdb_edit import EditWorkbook, diff_sheet
from services.edit_sql_guard import validate_edit_sql
from services.excel_ingest import get_session_factory

MAX_RETRY = 2
_PROMPT = Path("agent/dataset_edit_agent/prompts/edit_sql_generator.md")
_PROMPT_CACHE: str | None = None


def _prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT.read_text(encoding="utf-8")
    return _PROMPT_CACHE


class _SQLDraft(BaseModel):
    sql: str
    reason: str = ""


# ───────────────────────── 同步:物化 / 试应用(走 to_thread)─────────────────────────
def _snapshot_with_ops(info: dict, active_ops: list[str],
                       active_sheet: str | None = None) -> tuple[str, list[str]]:
    """渲染"当前数据"(各 sheet 列 + 前几行样例 + 当前 sheet 真实值参考)给 LLM 参考,
    并返回当前所有 sheet 名。

    sheet 名含会话内新建的汇总表 → 调用方据此构造可引用表集合(否则读不了自己建的汇总表)。
    """
    wb = EditWorkbook.from_dataset(info)
    try:
        wb.replay(active_ops)
        sheets = wb.sheets()
        lines: list[str] = []
        for s in sheets:
            prev = wb.preview(s, page=0, size=5)
            cols = ", ".join(f'"{c}"' for c in prev["columns"])
            lines.append(f'### Sheet "{s}"(当前 {prev["total"]} 行)\n列:{cols}')
            for r in prev["rows"][:5]:
                lines.append("  样例:" + json.dumps(r, ensure_ascii=False, default=str))
            # 显式标出汇总行(合计/小计…),让 LLM 聚合时能 WHERE 排除、或定位更新它
            for e in (wb.lineage.get(s) or {}).get("extra_rows") or []:
                vals = e.get("values") or {}
                lines.append("  汇总行(聚合时用 WHERE 排除它):"
                             + json.dumps(vals, ensure_ascii=False, default=str))
        # 当前选中 sheet 的真实值参考(精确匹配 WHERE 用;含本会话新值)
        target = active_sheet if active_sheet in sheets else (sheets[0] if sheets else None)
        if target:
            hints = wb.value_hints(target)
            if hints:
                lines.append(f'\n## 「{target}」各列真实值参考(WHERE 用,优先用这些精确值)')
                for col, vals in hints.items():
                    lines.append(f'- "{col}": ' + ", ".join(vals))
        return ("\n".join(lines) or "(无数据)"), sheets
    finally:
        wb.close()


def _apply_and_diff(info: dict, active_ops: list[str], new_sql: str,
                    target: str | None) -> dict:
    """在一次性副本上 replay(active + new),试执行(= 绑定校验)+ 算 diff + 出预览。"""
    before = EditWorkbook.from_dataset(info)
    after = EditWorkbook.from_dataset(info)
    try:
        before.replay(active_ops)
        after.replay(active_ops)
        try:
            after.replay([new_sql])
        except Exception as exc:  # 执行失败 = 绑定/语法问题
            return {"ok": False, "error": str(exc)}
        # target 为空(理论上不该,校验已要求单表)→ 取第一个 sheet 兜底
        sheet = target if target in after.sheets() else (after.sheets()[0] if after.sheets() else None)
        if sheet is None:
            return {"ok": False, "error": "无可用 sheet"}
        # 汇总/生成的 sheet(无血缘、无 __row_id)→ 不做单元格 diff,当作"已生成汇总表"。
        # 用"是否有血缘"判,而非"before 里有没有"——否则二次重建已存在的汇总表会误走 diff 崩。
        if sheet not in after.lineage:
            return {"ok": True, "created": True, "sheet": sheet,
                    "preview": after.preview(sheet), "rows": int(len(after.current(sheet)))}
        diff = diff_sheet(before.current(sheet), after.current(sheet), after.lineage.get(sheet))
        # 有新增行 → 预览跳到末页(新行追加在表尾,这样用户一眼看到);否则给第 0 页
        pv = after.preview(sheet)
        if diff["new_rows"] and pv["pages"] > 1:
            pv = after.preview(sheet, page=pv["pages"] - 1)
        return {"ok": True, "diff": diff, "preview": pv, "sheet": sheet}
    finally:
        before.close()
        after.close()


def _summary(diff: dict) -> dict:
    return {
        "changed": len(diff.get("cell_changes", [])),
        "deleted": len(diff.get("deleted", [])),
        "new_rows": len(diff.get("new_rows", [])),
        "added_cols": diff.get("added_cols", []),
        "dropped_cols": diff.get("dropped_cols", []),
        "renames": [f'{r["old"]}→{r["new"]}' for r in diff.get("renames", [])],
    }


# ───────────────────────── LLM:生成 / 修正 ─────────────────────────
async def _gen(instruction: str, current_md: str, active_sheet: str | None = None,
               last_sql: str = "", issues: list[str] | None = None) -> _SQLDraft:
    structured = llm.with_structured_output(_SQLDraft, method="json_mode")
    # 当前选中的 sheet = 默认操作对象(点哪个 tab 就改哪个),除非用户明确点名别的表
    scope = (
        f"# 当前选中的 sheet:「{active_sheet}」\n"
        f"**默认所有操作都针对这个 sheet**(聚合则 FROM 它生成新汇总表);"
        f"只有当用户在指令里明确点名了其它 sheet 时才操作那个。\n\n"
    ) if active_sheet else ""
    if issues:
        user = (scope + f"# 当前数据\n{current_md}\n\n# 你上一轮的 SQL(有问题)\n{last_sql}\n\n"
                f"# 校验/执行报告(逐条修复)\n" + "\n".join(f"- {x}" for x in issues) +
                f"\n\n# 用户指令\n{instruction}\n\n请输出修正后的完整 DuckDB SQL(JSON)。")
    else:
        user = (scope + f"# 当前数据(各 sheet 结构 + 样例)\n{current_md}\n\n"
                f"# 用户指令\n{instruction}\n\n请输出一条或多条 DuckDB SQL(JSON)。")
    return await structured.ainvoke([SystemMessage(content=_prompt()), HumanMessage(content=user)])  # type: ignore


# ───────────────────────── 主流程 ─────────────────────────
async def run_edit_message(
    dataset_id: int, session_id: int, instruction: str, confirmed: bool,
    active_sheet: str | None = None,
) -> AsyncIterator[WSStepInfo]:
    info = await get_dataset_info(dataset_id)
    if info is None or info.get("status") != "ready":
        yield WSStepInfo(step="加载数据", status="error",
                         data={"error": "数据集不存在或不可用"}, finish=True)
        return
    # 原始数据 sheet:不可被 CREATE 覆盖(protected)
    data_sheets = set((info.get("schema") or {}).get("sheets", {}).keys())

    # 取已应用的 op(重放基线)
    Session = get_session_factory()
    async with Session() as s:
        active_ops = await DatasetEditRepository(s).active_sql(session_id)

    yield WSStepInfo(step="理解指令", status="running")
    current_md, all_sheets = await asyncio.to_thread(
        _snapshot_with_ops, info, active_ops, active_sheet)
    # 可引用的表 = 当前会话的所有 sheet(含已建的汇总表)→ 否则读不了自己建的汇总表
    known_sheets = set(all_sheets) | data_sheets

    # 生成 → 校验 ⇄ 修正(含试执行绑定校验)。active_sheet = 用户当前选中的 tab
    draft = await _gen(instruction, current_md, active_sheet)
    sql = (draft.sql or "").strip()
    yield WSStepInfo(step="生成变更", status="success", data={"sql": sql, "reason": draft.reason})

    for attempt in range(MAX_RETRY + 1):
        check = validate_edit_sql(sql, known_sheets, protected_sheets=data_sheets)
        if not check.ok:
            if attempt >= MAX_RETRY:
                yield WSStepInfo(step="校验变更", status="error",
                                 data={"issues": check.issues}, finish=True)
                return
            yield WSStepInfo(step=f"修正变更(第 {attempt + 1} 次)", status="running",
                             data={"issues": check.issues})
            draft = await _gen(instruction, current_md, active_sheet, sql, check.issues)
            sql = (draft.sql or "").strip()
            continue

        # 试执行(绑定校验)+ 算 diff
        result = await asyncio.to_thread(
            _apply_and_diff, info, active_ops, check.normalized_sql, check.target_sheet)
        if not result["ok"]:
            if attempt >= MAX_RETRY:
                yield WSStepInfo(step="校验变更", status="error",
                                 data={"error": result["error"]}, finish=True)
                return
            yield WSStepInfo(step=f"修正变更(第 {attempt + 1} 次)", status="running",
                             data={"error": result["error"]})
            draft = await _gen(instruction, current_md, active_sheet, sql, [f"执行失败:{result['error']}"])
            sql = (draft.sql or "").strip()
            continue

        sheet, preview = result["sheet"], result["preview"]

        if result.get("created"):
            # 新建汇总 sheet:无单元格 diff,摘要标 created_sheet + 行数
            summary = {"changed": 0, "deleted": 0, "new_rows": 0, "added_cols": [],
                       "dropped_cols": [], "renames": [],
                       "created_sheet": sheet, "rows": result["rows"]}
            affected = summary
            diff_event = None
        else:
            diff = result["diff"]
            summary = _summary(diff)
            affected = {  # 摘要 + 明细 changes,供刷新后历史还原"列:旧→新"
                **summary,
                "changes": [
                    {"col": c["col"], "old": c["old"], "new": c["new"]}
                    for c in diff["cell_changes"][:20]
                ],
            }
            diff_event = {  # 截断,避免超大
                "cell_changes": diff["cell_changes"][:100],
                "deleted": diff["deleted"][:100],
                "renames": diff["renames"],
                "added_cols": diff["added_cols"], "dropped_cols": diff["dropped_cols"],
            }

        # 破坏性且未确认 → 发待确认卡(带真实影响数),不落库(建汇总表不会触发)
        if check.needs_confirm and not confirmed:
            yield WSStepInfo(step="待确认", status="success", finish=True, data={
                "needs_confirm": True, "sql": check.normalized_sql,
                "op_type": check.op_type, "target_sheet": sheet, "summary": summary,
                "hint": "该操作未限定范围,影响较大,确认后再执行",
            })
            return

        async with Session() as s:
            repo = DatasetEditRepository(s)
            await repo.add_op(session_id, nl=instruction, sql=check.normalized_sql,
                              op_type=check.op_type, target_sheet=sheet, affected=affected)
            await repo.touch(session_id)
            await s.commit()

        yield WSStepInfo(step="应用变更", status="success", finish=True, sql=check.normalized_sql, data={
            "summary": summary, "diff": diff_event, "preview": preview, "sheet": sheet,
        })
        return
