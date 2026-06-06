"""校验节点:静态安全校验(edit_sql_guard)+ 在一次性副本上试执行(绑定校验)+ 算 diff。

四种出口(由 graph 的 _route_after_validate 据 state 决定):
  · 纯查询(SELECT)→ 发引导卡,terminal=True → END
  · 有问题且重试未尽 → sql_issues 非空 → correct_sql
  · 有问题且重试用尽 → 发 error 卡,terminal=True → END
  · 通过 → 把 diff/摘要/预览/affected 写回 state → apply_edit
"""
import asyncio

from langgraph.runtime import Runtime

from agent.dataset_edit_agent.nodes._common import (MAX_RETRY, apply_and_diff, summarize)
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState
from agent.schemas import WSStepInfo
from services.dataset_loader import get_dataset_info
from services.edit_sql_guard import validate_edit_sql


async def validate_sql(state: DatasetEditState, runtime: Runtime[DatasetEditContext]):
    writer = runtime.stream_writer
    sql = (state.generated_sql or "").strip()
    known = set(state.known_sheets)
    protected = set(state.data_sheets)

    check = validate_edit_sql(sql, known, protected_sheets=protected)

    # 静态校验未过
    if not check.ok:
        if state.edit_retry >= MAX_RETRY:
            writer(WSStepInfo(step="校验变更", status="error",
                              data={"issues": check.issues}, finish=True))
            return {"terminal": True, "sql_issues": check.issues}
        return {"sql_issues": check.issues}  # → correct_sql

    # 纯查询 → 引导去问数,不执行
    if check.op_type == "select":
        writer(WSStepInfo(step="提示", status="success", finish=True, data={
            "guidance": "这看起来是查询 / 分析类问题。智能助手只负责「改数据」——"
                        "查询、统计分析请用「开启问数」;想看当前数据直接翻左边预览表即可。"}))
        return {"terminal": True}

    # 试执行(绑定校验)+ 算 diff
    info = await get_dataset_info(state.dataset_id, with_lineage=True)
    result = await asyncio.to_thread(
        apply_and_diff, info, state.active_ops, check.normalized_sql, check.target_sheet)
    if not result["ok"]:
        if state.edit_retry >= MAX_RETRY:
            writer(WSStepInfo(step="校验变更", status="error",
                              data={"error": result["error"]}, finish=True))
            return {"terminal": True}
        return {"sql_issues": [f"执行失败:{result['error']}"]}  # → correct_sql

    sheet, preview = result["sheet"], result["preview"]
    if result.get("created"):
        summary = {"changed": 0, "deleted": 0, "new_rows": 0, "added_cols": [],
                   "dropped_cols": [], "renames": [],
                   "created_sheet": sheet, "rows": result["rows"]}
        affected, diff_event = summary, None
    else:
        diff = result["diff"]
        summary = summarize(diff)
        affected = {**summary, "changes": [
            {"col": c["col"], "old": c["old"], "new": c["new"]}
            for c in diff["cell_changes"][:20]]}
        diff_event = {
            "cell_changes": diff["cell_changes"][:100],
            "deleted": diff["deleted"][:100],
            "renames": diff["renames"],
            "added_cols": diff["added_cols"], "dropped_cols": diff["dropped_cols"],
            # 新增行的 row_id(与预览的 row_ids 对得上)→ 前端整行高亮
            "new_rows": [r["row_id"] for r in diff["new_rows"]][:100]}

    return {
        "sql_issues": [], "normalized_sql": check.normalized_sql,
        "op_type": check.op_type, "target_sheet": sheet, "needs_confirm": check.needs_confirm,
        "created": bool(result.get("created")),
        "edit_summary": summary, "edit_diff": diff_event,
        "edit_affected": affected, "edit_preview": preview,
    }
