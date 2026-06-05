"""生成 SQL 节点:取已应用 op → 物化当前数据快照 → LLM 生成 DML/DDL。

快照(current_md)、可引用表(known_sheets)、active_ops 都写回 state,供后续校验/修正/应用复用。
"""
import asyncio

from langgraph.runtime import Runtime

from agent.dataset_edit_agent.nodes import latest_user_query
from agent.dataset_edit_agent.nodes._common import gen_sql, snapshot_with_ops
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState
from agent.schemas import WSStepInfo
from repositories.dataset_edit import DatasetEditRepository
from services.dataset_loader import get_dataset_info
from services.excel_ingest import get_session_factory


async def generate_sql(state: DatasetEditState, runtime: Runtime[DatasetEditContext]):
    writer = runtime.stream_writer

    Session = get_session_factory()
    async with Session() as s:
        active_ops = await DatasetEditRepository(s).active_sql(state.session_id)

    info = await get_dataset_info(state.dataset_id)
    current_md, all_sheets = await asyncio.to_thread(
        snapshot_with_ops, info, active_ops, state.active_sheet)
    # 可引用表 = 当前会话所有 sheet(含已建汇总表)+ 原始数据 sheet
    known = list({*all_sheets, *state.data_sheets})

    instruction = latest_user_query(state.messages)
    draft = await gen_sql(instruction, current_md, state.active_sheet)
    sql = (draft.sql or "").strip()
    writer(WSStepInfo(step="生成变更", status="success", data={"sql": sql, "reason": draft.reason}))

    return {"active_ops": active_ops, "current_md": current_md, "known_sheets": known,
            "generated_sql": sql, "edit_retry": 0, "sql_issues": []}
