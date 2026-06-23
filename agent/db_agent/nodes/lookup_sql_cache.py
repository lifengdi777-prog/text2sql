"""查 SQL 缓存节点。

位置:parse_query_intention(已把追问改写成自包含 standalone_query)之后。
拿 改写后的问题 + 数据源 + 库 + 该源 meta_version 算出缓存键去 sql_cache 表找:
  · 命中 → 把缓存 SQL 写进 state,标记 from_cache=True;图路由会跳过
    提词/召回/过滤/选路/生成/扇出检测一大段,直接去 validate_sql → execute_sql(重新查库,数据实时)。
  · 未命中 → 只带回 cache_key/meta_version(from_cache=False),图路由进入原完整生成流程;
    执行成功后由 execute_sql 用这个 cache_key 写回缓存。

任何异常都吞掉、按"未命中"处理 —— 缓存是加速层,坏了只退化成"没缓存",绝不影响主流程。
"""
from langgraph.runtime import Runtime

from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger
from repositories.datasource import DatasourceRepository
from repositories.sql_cache import SqlCacheRepository
from services.sql_cache import make_cache_key


async def lookup_sql_cache(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    ctx = runtime.context

    # eval/实验场景显式关掉缓存:当未命中处理,且 cache_key=None ——
    # 后续 execute_sql 的写回守卫(not state.cache_key)会一并跳过,做到既不读也不写。
    if not ctx.use_sql_cache:
        logger.info(f"SQL缓存已禁用(use_sql_cache=False) ds={ctx.datasource_id},走完整重新生成")
        return {"from_cache": False, "cache_key": None, "meta_version": None}

    # parse_query_intention 已原地替换 messages[-1] 为改写后的自包含问题
    question = state.messages[-1].content if state.messages else ""

    try:
        async with ctx.meta_db_client.session() as session:
            ds = await DatasourceRepository(session).get_by_id(ctx.datasource_id)
            meta_version = (ds.meta_version if ds else 1) or 1
            key = make_cache_key(question, ctx.datasource_id, ctx.database, meta_version)
            cached_sql = await SqlCacheRepository(session).get_sql(key)
            await session.commit()  # 落 hit_count / last_hit_at
    except Exception as e:  # noqa: BLE001
        logger.warning(f"查SQL缓存失败,按未命中处理:{e}")
        return {"from_cache": False, "cache_key": None, "meta_version": None}

    if cached_sql:
        runtime.stream_writer(WSStepInfo(step="命中SQL缓存(跳过生成)", status="success"))
        logger.info(f"SQL缓存命中 key={key[:12]} ds={ctx.datasource_id} v={meta_version}")
        return {"sql": cached_sql, "from_cache": True, "cache_key": key,
                "meta_version": meta_version, "error": None}

    logger.info(f"SQL缓存未命中 key={key[:12]} ds={ctx.datasource_id} v={meta_version}")
    return {"from_cache": False, "cache_key": key, "meta_version": meta_version}
