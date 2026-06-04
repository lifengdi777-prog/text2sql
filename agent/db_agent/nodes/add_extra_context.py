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
    # 取各列的描述给等式标上语义。来源是 meta 库 column_info.description(经召回/补全带进 table_infos),
    # 不是 data_relationship.description(那是边的备注、可能滞后);用 column_info 保证单一真相源。
    # 同一对表有多条连接(如发货地区/账单地区)时,大模型靠这括号里的含义 + 用户问题选对应的那条。
    col_desc = {
        (t.id, c.name): c.description
        for t in table_infos for c in t.columns
    }
    join_lines = []
    for r in relationships:
        if r.from_table not in ids or r.to_table not in ids:
            continue
        line = f"{r.from_table}.{r.from_column} = {r.to_table}.{r.to_column}"
        # 优先用外键列(from)的描述;缺失再退用被引用列(to)的
        desc = col_desc.get((r.from_table, r.from_column)) or col_desc.get((r.to_table, r.to_column))
        if desc:
            line += f"  （{desc}）"
        join_lines.append(line)
    if join_lines:
        db_info = f"{db_info}\n## 表之间的连接关系(JOIN 连接列优先严格按以下等式;括号内是该连接列的含义;未列出的连接再依据列描述合理推断):\n" + \
            "\n".join(f"- {line}" for line in join_lines) + \
            "\n注意:同一对表之间若列出多条连接关系(如下单日期/付款日期/发货日期分别指向日期表),必须根据括号内的字段含义结合用户问题只选其中一条来 JOIN,切勿同时用多条连接同一对表(否则会重复关联、造成行数膨胀或语义错误)。"

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