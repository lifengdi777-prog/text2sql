"""执行 SQL 节点(替代旧的 execute_spec)。

把数据集各 sheet 注册成 DuckDB 视图,跑 validate_sql 规范化后的 SELECT,得到 rows。
rows 写进 state.sql_result(继承自 WSAgentState),下游 chart_agent / interpret_result 原样消费。
"""
from langgraph.runtime import Runtime

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.schemas import WSStepInfo
from core.log import logger
from services.duckdb_exec import query_sql


async def execute_sql(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="执行查询", status="running"))

    if state.error:
        return {}
    sql = (state.generated_sql or "").strip()
    if not sql:
        msg = "缺少可执行的 SQL"
        writer(WSStepInfo(step="执行查询", status="error", data={"error": msg}))
        return {"error": msg}
    if state.dataset_id is None:
        msg = "缺少 dataset_id"
        writer(WSStepInfo(step="执行查询", status="error", data={"error": msg}))
        return {"error": msg}

    try:
        rows = await query_sql(state.dataset_id, sql)
    except Exception as exc:
        logger.exception(f"execute_sql 失败:{exc}")
        msg = f"执行查询失败:{exc}"
        writer(WSStepInfo(step="执行查询", status="error", data={"error": msg}))
        return {"error": msg}

    logger.info(f"execute_sql 完成:dataset={state.dataset_id} 返回 {len(rows)} 行")
    # data=rows + finish=True:前端据此填 message.result(表格视图 / 本地切图都依赖原始行)
    writer(WSStepInfo(step="执行查询", status="success", data=rows, finish=True))
    return {"sql_result": rows, "error": None}
