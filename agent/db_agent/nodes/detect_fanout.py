from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.db_agent.join_path import detect_fanout as _detect_fanout
from core.log import logger


async def detect_fanout(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """扇出检测：连接路径定型后，判断"事实表→分组维度"是否含一对多/多对多跳。
    若有，写入 state.fanout_warning，下游 add_extra_context 会把它一并交给 generate_sql，
    避免默默重复计算（如"各供应商的实际产量"这类无法唯一归因的伪命题）。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="扇出检测", status="running"))

    table_infos = state.table_infos or []
    async with runtime.context.meta_repo() as meta_repo:
        relationships = await meta_repo.get_relationships()
    warning = _detect_fanout(table_infos, relationships)

    writer(WSStepInfo(step="扇出检测", status="success", data=warning))
    if warning:
        logger.info(f"扇出检测：{warning}")
    return {"fanout_warning": warning}
