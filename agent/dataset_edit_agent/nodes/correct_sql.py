"""修正节点:带校验/执行报告让 LLM 重写 SQL,retry 计数 +1,回到 validate 重判。"""
from langgraph.runtime import Runtime

from agent.dataset_edit_agent.nodes import latest_user_query
from agent.dataset_edit_agent.nodes._common import gen_sql
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState
from agent.schemas import WSStepInfo


async def correct_sql(state: DatasetEditState, runtime: Runtime[DatasetEditContext]):
    writer = runtime.stream_writer
    n = state.edit_retry + 1
    writer(WSStepInfo(step=f"修正变更(第 {n} 次)", status="running",
                      data={"issues": state.sql_issues}))

    instruction = latest_user_query(state.messages)
    draft = await gen_sql(instruction, state.current_md, state.active_sheet,
                          last_sql=state.generated_sql or "", issues=state.sql_issues)
    return {"generated_sql": (draft.sql or "").strip(), "edit_retry": n, "sql_issues": []}
