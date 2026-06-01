from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo, WSAgentTableInfoState
from agent.prompts import load_prompt
from agent.llm import fast_llm
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
    chain = prompt_template | fast_llm | JsonOutputParser()
    result = await chain.ainvoke({"query": query, "table_infos": [table_info.model_dump() for table_info in table_infos]})
    #如果合并返回的表信息没有在result里，就从table_infos里删除；
    #如果表信息里的字段没有在result里，就从table_info.columns里删除。
    for table_info in table_infos[:]:
        if table_info.name not in result:
            table_infos.remove(table_info)
            continue
        column_names = result[table_info.name]
        for column_info in table_info.columns[:]:
            if column_info.name not in column_names:
                table_info.columns.remove(column_info)
    
    writer(WSStepInfo(step="过滤数据库表信息", status="success"))
    logger.info([
        (table_info.name, [column_info.name for column_info in table_info.columns])
        for table_info in table_infos
    ])
    return {"table_infos": table_infos}