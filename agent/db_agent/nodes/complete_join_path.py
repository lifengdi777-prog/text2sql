from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.db_agent.join_path import complete_join_path as _complete_join_path
from core.log import logger


async def complete_join_path(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """filter 之后再补一次连接路径：filter 会把没被问到列的中间表(如 workshop)剪掉，
    这里基于外键关系把"连接必需"的中间表加回来，保证 generate_sql 能写出完整的多跳 JOIN。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="补全连接路径", status="running"))

    table_infos = state.table_infos or []
    async with runtime.context.meta_repo() as meta_repo:
        table_infos = await _complete_join_path(table_infos, meta_repo)

    writer(WSStepInfo(step="补全连接路径", status="success"))
    logger.info([
        (table_info.name, [column_info.name for column_info in table_info.columns])
        for table_info in table_infos
    ])
    return {"table_infos": table_infos}
