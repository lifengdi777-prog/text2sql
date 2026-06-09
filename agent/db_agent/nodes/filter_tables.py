from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo, WSAgentTableInfoState
from agent.prompts import load_prompt
from agent.llm import llm
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from core.log import logger


async def filter_tables(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="过滤数据库表信息", status="running"))

    query = state.messages[-1].content
    #如果为空则直接返回空的表信息列表
    table_infos = state.table_infos or []

    prompt = await load_prompt("filter_table_info")
    #将输入变量（query、table_infos）填充到 prompt 模板中，生成完整的提示词
    prompt_template = PromptTemplate(template=prompt, input_variables=['query', 'table_infos'])
    #定义了一个langchian的链式调用，包含了提示词模板、LLM调用和JSON输出解析三个步骤。
    #JsonOutputParser()	的作用是把LLM返回的文本解析成JSON格式，方便后续处理。
    chain = prompt_template | llm | JsonOutputParser()
    result = await chain.ainvoke({"query": query, "table_infos": [table_info.model_dump() for table_info in table_infos]})

    # 形状校验 + 失败放行:result 期望是 {表名: [列名]}。llm 偶发返回 list / 多包一层 /
    # 用 id 当 key,会让所有表名都匹配不上而被静默清空,甚至 result[name] 对 list 取下标直接崩。
    # 与其给下游一个空表集(generate_sql 会写出空/瞎编 SQL),不如放行保留召回表,交后续节点取舍。
    if not isinstance(result, dict):
        logger.warning(f"filter_tables 输出非字典(实为 {type(result).__name__}),跳过过滤、保留召回表")
        writer(WSStepInfo(step="过滤数据库表信息", status="success"))
        return {"table_infos": table_infos}

    #如果合并返回的表信息没有在result里，就从table_infos里删除；
    #如果表信息里的字段没有在result里，就从table_info.columns里删除。
    for table_info in table_infos[:]:
        if table_info.name not in result:
            table_infos.remove(table_info)
            continue
        column_names = result[table_info.name]
        if not isinstance(column_names, list):
            continue  # 该表列清单格式异常 → 保留全部列,不按它收窄(JOIN 列另由 complete_join_path 兜底)
        for column_info in table_info.columns[:]:
            if column_info.name not in column_names:
                table_info.columns.remove(column_info)

    # 全删多为"表名/格式不匹配"所致(也可能 LLM 真判定全不相关),把静默清空变成可见告警,便于排查空 SQL。
    if not table_infos:
        logger.warning("filter_tables 过滤后无表,可能是 LLM 输出格式/表名不匹配,下游 SQL 可能为空")

    writer(WSStepInfo(step="过滤数据库表信息", status="success"))
    logger.info([
        (table_info.name, [column_info.name for column_info in table_info.columns])
        for table_info in table_infos
    ])
    return {"table_infos": table_infos}