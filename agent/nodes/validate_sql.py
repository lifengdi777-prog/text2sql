from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from core.log import logger

#判断SQL语句是否有语法错误，是否符合规范。
async def validate_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="校验SQL语句", status="running"))

    sql = state.sql or ""

    dw_db_repo = runtime.context.dw_db_repo

    try:
        await dw_db_repo.validate_sql(sql)
        writer(WSStepInfo(step="校验SQL语句", status="success"))
        logger.info("sql校验成功！")
        #没有错误就清空error字段，继续执行SQL；
        return {"error": None}
    except Exception as e:
        logger.info(f"sql校验失败！错误信息：{e}")
        writer(WSStepInfo(step="校验SQL语句", status="error"))
        #如果有错误，就把错误信息放到error字段里，进入校正流程。
        return {"error": str(e)}