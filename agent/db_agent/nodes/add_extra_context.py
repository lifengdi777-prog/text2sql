from csv import writer

from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from datetime import datetime
from core.log import logger


async def add_extra_context(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="添加额外信息", status="running"))

    # 短会话:只在读 db_info 时占用连接,用完即还
    async with runtime.context.dw_repo() as dw_db_repo:
        db_infos = await dw_db_repo.get_db_info()
    db_info = f"当前数据库类型：{db_infos['dialect']}，编码：{db_infos['charset']}，版本号：{db_infos['version']}"
    # 若扇出检测发现"事实度量经一对多/多对多关系聚合到某维度会重复计算"，把警告随上下文一并交给
    # generate_sql(沿用现有 {db_info} 槽位，不改提示词模板)，提醒它避免直接 SUM 或改用可归因口径。
    if state.fanout_warning:
        db_info = f"{db_info}\n{state.fanout_warning}"

    # 表连接关系:把当前表集合涉及的外键边(声明的 + ER 图人工维护的,统一来自 data_relationship)
    # 显式交给 generate_sql,让它照等式写 JOIN ON,而不是靠列描述/命名去猜
    # ——尤其数据源没建 FK 约束、关系全靠人工 ER 维护时,这一步是 JOIN 写对的关键。
    table_infos = state.table_infos or []
    ids = {t.id for t in table_infos}
    async with runtime.context.meta_repo() as meta_repo:
        relationships = await meta_repo.get_relationships()
    join_lines = [
        f"{r.from_table}.{r.from_column} = {r.to_table}.{r.to_column}"
        for r in relationships
        if r.from_table in ids and r.to_table in ids
    ]
    if join_lines:
        db_info = f"{db_info}\n## 表之间的连接关系(JOIN 连接列优先严格按以下等式;若某个需要的连接未在下方列出,再依据列描述合理推断):\n" + \
            "\n".join(f"- {line}" for line in join_lines)

    # 获取当前时间
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday = now.weekday() + 1
    quarter = f"Q{(now.month-1) // 3 + 1}"
    date_info = f"当前时间为：{now_str}，星期{weekday}，第{quarter}季度"
    
    writer(WSStepInfo(step="添加额外信息", status="success"))
    logger.info(db_info)
    logger.info(date_info)
    #在 LangGraph 中，节点函数不需要手动调用任何 set 方法，
    # 只需要 return 一个字典，LangGraph 会自动将返回值合并到 State 中。
    return {'db_info': db_info, "date_info": date_info}