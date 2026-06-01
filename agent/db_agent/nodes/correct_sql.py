from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agent.prompts import load_prompt
from agent.llm import llm
from core.log import logger

#负责在 SQL 执行出错时，将原始SQL + 错误信息 + 上下文一起交给 LLM，让它修复 SQL 并将修正结果更新回状态中。
#拿着有问题的 SQL 和错误信息，只改出错的地方，不改业务逻辑，返回一条修复好的纯文本 SQL。
#以 error 报错信息为线索，精准定位出错位置，只动那一处，其余全部保持原样。
async def correct_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="校正SQL语句", status="running"))

    sql = state.sql
    #错误信息
    error = state.error

    query = state.messages[-1].content
    table_infos = state.table_infos
    metric_infos = state.metric_infos
    date_info = state.date_info
    db_info = state.db_info
    #plan_joins 规划出的 JOIN 子句(强约束)，修复时同样需遵循，避免改错连接条件。
    join_clauses = state.join_clauses or "（本次查询无预设 JOIN 关系）"

    prompt = await load_prompt("correct_sql")
    prompt_template = PromptTemplate(template=prompt, input_variables=['query', 'sql', 'error', 'table_infos', 'metric_infos', 'date_info', 'db_info', 'join_clauses'])
    chain = prompt_template | llm | StrOutputParser()
    result = await chain.ainvoke({
        "query": query,
        "sql": sql,
        "error": error,
        "table_infos": table_infos,
        "metric_infos": metric_infos,
        "date_info": date_info,
        "db_info": db_info,
        "join_clauses": join_clauses
    })

    writer(WSStepInfo(step="校正SQL语句", status="success"))
    logger.info(f"校正后的SQL：{result}")
    #记录已校正次数，供 graph 路由判断是否超过重试上限，避免无限循环。
    return {"sql": result, "correct_attempts": state.correct_attempts + 1}