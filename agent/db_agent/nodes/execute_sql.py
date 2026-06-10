from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.db_agent.nodes.validate_sql import MAX_RESULT_ROWS
from core.log import logger
from repositories.sql_cache import SqlCacheRepository
from services.sql_cache import normalize_question


async def _save_sql_cache(state: WSAgentState, runtime: Runtime[WSAgentContext], sql: str) -> None:
    """把本轮(新生成、且执行成功)的 问题→SQL 写回缓存。

    只在「未命中缓存」(from_cache=False)且有 cache_key 时写 —— 命中的本就在缓存里不必重写,
    澄清打断/校验失败/执行报错的根本到不了这里。任何异常都吞掉,绝不影响已成功的查询返回。
    """
    if state.from_cache or not state.cache_key:
        return
    try:
        question = normalize_question(state.messages[-1].content if state.messages else "")
        async with runtime.context.meta_db_client.session() as session:
            async with session.begin():
                await SqlCacheRepository(session).put(
                    cache_key=state.cache_key,
                    datasource_id=runtime.context.datasource_id,
                    meta_version=state.meta_version or 1,
                    question=question,
                    sql=sql,
                )
        logger.info(f"写回SQL缓存 key={state.cache_key[:12]}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"写SQL缓存失败(不影响返回):{e}")


async def execute_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="执行SQL语句", status="running"))

    sql = state.sql or ""

    try:
        # 短会话:只在跑这条 SELECT 时占用连接
        async with runtime.context.dw_repo() as dw_db_repo:
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
        # 执行成功 → 若是本轮新生成的(非命中)就把 问题→SQL 写回缓存,供后续相同问题复用。
        await _save_sql_cache(state, runtime, sql)
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