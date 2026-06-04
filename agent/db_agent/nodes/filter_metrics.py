from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.prompts import load_prompt
from agent.llm import fast_llm
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
    chain = prompt_template | fast_llm | JsonOutputParser()
    result = await chain.ainvoke({"query": query, "metric_infos": [metric_info.model_dump() for metric_info in metric_infos]})

    # 形状校验 + 失败放行:这里只用 `name not in result` 做成员判断,dict(判 key)/list(判元素)都成立;
    # 但若是 None/字符串等其它类型,in 会崩或做错误的子串匹配,故非 dict/list 一律放行保留召回指标。
    # (指标过滤后为空很常见且多为正常——很多查询本就无指标,故不对"空"额外告警,避免日志噪音。)
    if not isinstance(result, (dict, list)):
        logger.warning(f"filter_metrics 输出非字典/列表(实为 {type(result).__name__}),跳过过滤、保留召回指标")
        writer(WSStepInfo(step="过滤指标信息", status="success"))
        return {"metric_infos": metric_infos}

    for metric_info in metric_infos[:]:
        if metric_info.name not in result:
            metric_infos.remove(metric_info)

    writer(WSStepInfo(step="过滤指标信息", status="success"))
    logger.info(f"过滤之后：{metric_infos}")
    return {"metric_infos": metric_infos}