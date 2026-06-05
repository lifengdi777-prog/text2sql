"""应用节点:破坏性未确认 → 发待确认卡(不落库);否则落 op 日志 + 发预览/diff。"""
from langgraph.runtime import Runtime

from agent.dataset_edit_agent.nodes import latest_user_query
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState
from agent.schemas import WSStepInfo
from repositories.dataset_edit import DatasetEditRepository
from services.excel_ingest import get_session_factory


async def apply_edit(state: DatasetEditState, runtime: Runtime[DatasetEditContext]):
    writer = runtime.stream_writer
    sheet = state.target_sheet
    summary = state.edit_summary or {}

    # 破坏性且未确认 → 待确认卡(带真实影响数),不落库
    if state.needs_confirm and not state.confirmed:
        writer(WSStepInfo(step="待确认", status="success", finish=True, data={
            "needs_confirm": True, "sql": state.normalized_sql,
            "op_type": state.op_type, "target_sheet": sheet, "summary": summary,
            "hint": "该操作未限定范围,影响较大,确认后再执行"}))
        return {}

    # 落 op 日志
    instruction = latest_user_query(state.messages)
    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        await repo.add_op(state.session_id, nl=instruction, sql=state.normalized_sql,
                          op_type=state.op_type or "", target_sheet=sheet,
                          affected=state.edit_affected)
        await repo.touch(state.session_id)
        await s.commit()

    writer(WSStepInfo(step="应用变更", status="success", finish=True, sql=state.normalized_sql,
                      data={"summary": summary, "diff": state.edit_diff,
                            "preview": state.edit_preview, "sheet": sheet}))
    return {}
