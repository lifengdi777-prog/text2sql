from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.prompts import load_prompt
from agent.llm import llm
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from core.log import logger


async def filter_metrics(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="过滤指标信息", status="running"))

    query = state.messages[-1].content

    metric_infos = state.metric_infos or []

    prompt = await load_prompt("filter_metric_info")
    prompt_template = PromptTemplate(template=prompt, input_variables=['query', 'metric_infos'])
    chain = prompt_template | llm | JsonOutputParser()
    result = await chain.ainvoke({"query": query, "metric_infos": [metric_info.model_dump() for metric_info in metric_infos]})

    for metric_info in metric_infos[:]:
        if metric_info.name not in result:
            metric_infos.remove(metric_info)

    writer(WSStepInfo(step="过滤指标信息", status="success"))
    logger.info(f"过滤之后：{metric_infos}")
    return {"metric_infos": metric_infos}