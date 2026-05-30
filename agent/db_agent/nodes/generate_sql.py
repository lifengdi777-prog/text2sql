from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.prompts import load_prompt
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agent.llm import llm
from core.log import logger


async def generate_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成SQL语句", status="running"))
    #用户的查询语句
    query = state.messages[-1].content
    #合并了召回和过滤之后的表信息
    table_infos = state.table_infos or []
    #合并了召回和过滤之后的指标信息
    metric_infos = state.metric_infos or []
    #当前日期时间上下文
    date_info = state.date_info
    #当前数据库信息
    db_info = state.db_info
    #plan_joins 规划出的 JOIN 子句(强约束)；为空时给出占位说明，让模型按需自行连接。
    join_clauses = state.join_clauses or "（本次查询无预设 JOIN 关系，如涉及多表请根据字段主外键角色合理连接）"


    prompt = await load_prompt("generate_sql")
    #定义提示词模板，指定输入变量列表。这个模板会被用来生成最终的提示词文本，输入变量会被替换成实际的值。
    prompt_template = PromptTemplate(template=prompt, input_variables=['query', "table_infos", 'metric_infos', 'date_info', 'db_info', 'join_clauses'])
    chain = prompt_template | llm | StrOutputParser()
    #异步执行整条 LangChain 链，把所有需要的数据一次性传入，获取最终结果。
    result = await chain.ainvoke({
        "query": query,
        #把组 Pydantic 对象转换成字典列表，然后传入提示词模板。因为提示词模板只能处理基本数据类型，不能直接处理复杂对象。
        "table_infos": [table_info.model_dump() for table_info in table_infos],
        "metric_infos": [metric_info.model_dump() for metric_info in metric_infos],
        "date_info": date_info,
        "db_info": db_info,
        "join_clauses": join_clauses
    })
    writer(WSStepInfo(step="生成SQL语句", status="success"))

    logger.info(f"sql: {result}")

    return {"sql": result}