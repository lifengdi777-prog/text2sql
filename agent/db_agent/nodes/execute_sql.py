from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.db_agent.nodes.validate_sql import MAX_RESULT_ROWS
from core.log import logger


async def execute_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="执行SQL语句", status="running"))

    sql = state.sql or ""
    dw_db_repo = runtime.context.dw_db_repo

    try:
        result = await dw_db_repo.execute_sql(sql)
        # validate_sql 注入了 LIMIT MAX_RESULT_ROWS+1:这里若拿到 >上限,说明结果被截断,
        # 截到上限并标记,供解读环节提示用户"仅展示前 N 行"。
        truncated = len(result) > MAX_RESULT_ROWS
        if truncated:
            result = result[:MAX_RESULT_ROWS]
            logger.info(f"SQL 结果超过 {MAX_RESULT_ROWS} 行,已截断")
        logger.info(f"SQL执行后的结果：{result}")
        # finish=True:让老前端拿到原始表格数据(后向兼容)。
        # 一次成功流里会有两次 finish=True:这里(data=Array,表格)+ chart_agent(data=Object,chart_config)
        # 前端按 data 类型分发:数组走 result,对象走 chartConfig
        writer(WSStepInfo(step="执行SQL语句", status="success", data=result, sql=sql, finish=True))
        return {"sql_result": result, "error": None, "truncated": truncated}
    except Exception as exc:
        # 失败时不发 finish:让 chart_agent 的 error 卡作为最终事件
        logger.exception(f"SQL执行失败：{exc}")
        writer(WSStepInfo(
            step="执行SQL语句",
            status="error",
            data={"error": str(exc)},
            finish=False,
        ))
        return {"sql_result": None, "error": str(exc)}