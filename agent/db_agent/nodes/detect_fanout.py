from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.db_agent.join_path import detect_fanout as _detect_fanout, extract_used_relationships
from core.log import logger


async def detect_fanout(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """扇出检测(SQL 生成之后)：从生成的 SQL 解析出 LLM【实际选用的连接路径】,
    只对这条路径判断"事实表→分组维度"是否含一对多/多对多跳。若有,写入 state.fanout_warning,
    图随即路由到 fanout_clarify 打断本轮、让用户重新明确口径——而不是默默重复计算
    (如"各供应商的实际产量"这类无法唯一归因的伪命题)。

    与早先"SQL 前按最短路径预判"不同:此处对应的是真正要执行的那条路径,不会因 LLM 选了
    另一条候选路径而误报/漏报。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="扇出检测", status="running"))

    table_infos = state.table_infos or []
    sql = state.sql or ""
    async with runtime.context.meta_repo() as meta_repo:
        relationships = await meta_repo.get_relationships()
    # 只取 SQL 里实际 JOIN 到的边,扇出判断便锁定在 LLM 实选路径上。
    used = extract_used_relationships(sql, table_infos, relationships, dialect="mysql")
    warning = _detect_fanout(table_infos, used)

    writer(WSStepInfo(step="扇出检测", status="success", data=warning))
    if warning:
        logger.info(f"扇出检测(实选路径)：{warning}")
    return {"fanout_warning": warning}
