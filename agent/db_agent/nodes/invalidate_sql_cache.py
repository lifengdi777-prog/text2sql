"""清除过期缓存节点(校验自愈)。

命中缓存的 SQL 进了 validate_sql 却没通过 —— 说明这条缓存已过期
(多半是某种没被 meta_version 捕捉到的库变更)。这里把这条坏缓存删掉,
并把 from_cache 置回 False、清空 error,随后图路由回到 extract_keywords
走完整重新生成;新生成且执行成功的 SQL 会由 execute_sql 重新写回缓存(自愈)。
"""
from langgraph.runtime import Runtime

from agent.schemas import WSAgentContext, WSAgentState
from core.log import logger
from repositories.sql_cache import SqlCacheRepository


async def invalidate_sql_cache(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    if state.cache_key:
        try:
            async with runtime.context.meta_db_client.session() as session:
                async with session.begin():
                    await SqlCacheRepository(session).delete(state.cache_key)
            logger.warning(f"缓存SQL校验失败,已清除过期缓存 key={state.cache_key[:12]},回退完整生成")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"清除过期缓存失败(不影响回退生成):{e}")
    # 置回非命中 + 清错误:让后续完整生成正常进行,且执行成功后会重新写回缓存
    return {"from_cache": False, "error": None}
