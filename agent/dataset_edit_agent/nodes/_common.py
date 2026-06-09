"""编辑子图各节点共用的辅助函数(从原 runner 原样搬来,行为不变)。

包含:意图分类、当前数据快照、试执行+diff、摘要、SQL 生成/修正所需的 prompt 与模型。
节点只是把这些拼成"图的形状",逻辑都在这里。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.llm import llm
from core.log import logger
from services.duckdb_edit import EditWorkbook, diff_sheet

MAX_RETRY = 2
_PROMPT = Path("agent/dataset_edit_agent/prompts/edit_sql_generator.md")
_INTENT_PROMPT = Path("agent/dataset_edit_agent/prompts/edit_intent.md")
_PROMPT_CACHE: dict[str, str] = {}

GUIDE_QUERY = "这看起来是查询 / 分析问题。智能助手只负责「改数据」——查询、统计请用「开启问数」;想看当前数据直接翻左边预览表即可。"
GUIDE_CHITCHAT = "你好!我是数据编辑助手,可以帮你改这份表 —— 比如改某个值、删符合条件的行、加一列、生成汇总。说说你想怎么改?"


def _read(path: Path) -> str:
    key = str(path)
    if key not in _PROMPT_CACHE:
        _PROMPT_CACHE[key] = path.read_text(encoding="utf-8")
    return _PROMPT_CACHE[key]


class SQLDraft(BaseModel):
    sql: str
    reason: str = ""


class EditIntent(BaseModel):
    kind: Literal["edit", "query", "chitchat"]
    reply: str = ""


def schema_brief(info: dict) -> str:
    """从 info 提取轻量结构(sheet: 列名…),供意图分类用,无需物化。"""
    sheets = (info.get("schema") or {}).get("sheets") or {}
    parts = []
    for name, s in sheets.items():
        cols = [str(c.get("name")) for c in (s.get("columns") or [])]
        parts.append(f"{name}: " + ", ".join(cols))
    return "\n".join(parts) or "(无结构)"


async def classify_intent(instruction: str, brief: str) -> EditIntent:
    """前置意图分类(轻量 llm):edit / query / chitchat。失败默认 edit,不误伤正常编辑。"""
    structured = llm.with_structured_output(EditIntent, method="json_mode")
    user = f"# 数据集结构\n{brief}\n\n# 用户输入\n{instruction}"
    try:
        return await structured.ainvoke([SystemMessage(content=_read(_INTENT_PROMPT)),
                                         HumanMessage(content=user)])  # type: ignore
    except Exception as exc:
        logger.warning(f"编辑意图分类失败,默认按编辑处理:{exc}")
        return EditIntent(kind="edit")


# ───────────────────────── 同步:物化 / 试应用(走 to_thread)─────────────────────────
def snapshot_with_ops(info: dict, active_ops: list[str],
                      active_sheet: str | None = None) -> tuple[str, list[str]]:
    """渲染"当前数据"(各 sheet 列 + 样例 + 当前 sheet 真实值参考),并返回当前所有 sheet 名。"""
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
            for e in (wb.lineage.get(s) or {}).get("extra_rows") or []:
                vals = e.get("values") or {}
                lines.append("  汇总行(聚合时用 WHERE 排除它):"
                             + json.dumps(vals, ensure_ascii=False, default=str))
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


def apply_and_diff(info: dict, active_ops: list[str], new_sql: str,
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
        sheet = target if target in after.sheets() else (after.sheets()[0] if after.sheets() else None)
        if sheet is None:
            return {"ok": False, "error": "无可用 sheet"}
        # 汇总/生成的 sheet(无血缘)→ 不做单元格 diff,当作"已生成汇总表"
        if sheet not in after.lineage:
            return {"ok": True, "created": True, "sheet": sheet,
                    "preview": after.preview(sheet), "rows": int(len(after.current(sheet)))}
        diff = diff_sheet(before.current(sheet), after.current(sheet), after.lineage.get(sheet))
        pv = after.preview(sheet)
        if diff["new_rows"] and pv["pages"] > 1:  # 有新增行 → 跳末页让用户看到
            pv = after.preview(sheet, page=pv["pages"] - 1)
        return {"ok": True, "diff": diff, "preview": pv, "sheet": sheet}
    finally:
        before.close()
        after.close()


def summarize(diff: dict) -> dict:
    return {
        "changed": len(diff.get("cell_changes", [])),
        "deleted": len(diff.get("deleted", [])),
        "new_rows": len(diff.get("new_rows", [])),
        "added_cols": diff.get("added_cols", []),
        "dropped_cols": diff.get("dropped_cols", []),
        "renames": [f'{r["old"]}→{r["new"]}' for r in diff.get("renames", [])],
    }


# ───────────────────────── LLM:生成 / 修正 ─────────────────────────
async def gen_sql(instruction: str, current_md: str, active_sheet: str | None = None,
                  last_sql: str = "", issues: list[str] | None = None) -> SQLDraft:
    structured = llm.with_structured_output(SQLDraft, method="json_mode")
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
    return await structured.ainvoke([SystemMessage(content=_read(_PROMPT)),
                                     HumanMessage(content=user)])  # type: ignore
