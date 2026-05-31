from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from core.log import logger


async def plan_joins(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """
    JOIN 路径规划节点。

    放在 filter_tables 之后、add_extra_context/generate_sql 之前。
    它不调用大模型，纯粹基于元数据中的外键关系(meta.join_relation)：
      1. 取出 filter_tables 选定的表集合；
      2. 查出连接这些表所需的 JOIN 边(两端都在已选表内)；
      3. 拼成确定性的 SQL JOIN 子句文本，写入 state.join_clauses。

    随后 generate_sql 会把该子句作为强约束注入提示词，
    让 LLM 不再自行推断 JOIN 条件，只负责选维度、写聚合、加过滤。
    """
    writer = runtime.stream_writer
    writer(WSStepInfo(step="规划JOIN路径", status="running"))

    table_infos = state.table_infos or []
    table_ids = [t.id for t in table_infos]

    # 单表或无表：无需 JOIN
    if len(table_ids) <= 1:
        writer(WSStepInfo(step="规划JOIN路径", status="success"))
        logger.info("plan_joins: 单表/无表，无需 JOIN")
        return {"join_clauses": None}

    # 短会话:只在查 JOIN 关系时占用连接
    async with runtime.context.meta_repo() as meta_db_repo:
        relations = await meta_db_repo.get_join_relations_by_table_ids(table_ids)

    if not relations:
        # 选了多张表却查不到关系：交回给 LLM 兜底，不强行造 JOIN
        writer(WSStepInfo(step="规划JOIN路径", status="success"))
        logger.info(f"plan_joins: 已选表 {table_ids} 间无显式外键关系，跳过强约束")
        return {"join_clauses": None}

    # 拼成 SQL JOIN 子句。源表(外键所在表，通常是事实表)作为主表，
    # 用 JOIN 把每张目标表(维表)接上。
    join_lines = [
        f"{rel.join_type.upper()} JOIN {rel.target_table} "
        f"ON {rel.source_table}.{rel.source_column} = {rel.target_table}.{rel.target_column}"
        for rel in relations
    ]
    join_clauses = "\n".join(join_lines)

    writer(WSStepInfo(step="规划JOIN路径", status="success", data=join_clauses))
    logger.info(f"plan_joins:\n{join_clauses}")

    return {"join_clauses": join_clauses}
