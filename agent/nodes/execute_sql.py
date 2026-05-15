from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from core.log import logger


async def execute_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="执行SQL语句", status="running"))

    sql = state.sql or ""
    dw_db_repo = runtime.context.dw_db_repo

    result = await dw_db_repo.execute_sql(sql)
    logger.info(f"SQL执行后的结果：{result}")

    writer(WSStepInfo(step="执行SQL语句", status="success", data=result, finish=True))