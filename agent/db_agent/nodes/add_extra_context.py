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