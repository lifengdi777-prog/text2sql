from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.prompts import load_prompt
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from agent.llm import llm
from dtos.meta import MetricInfo
from clients.embedding import embedding_client


async def recall_metrics(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="召回指标信息", status="running"))

    query = state.messages[-1].content

    context = runtime.context

    query = state.messages[-1].content
    keywords = state.keywords

    metric_qdrant_repo = context.metric_qdrant_repo

    # 先使用大模型拓展关键词
    prompt = await load_prompt("extend_keywords_for_metric_recall")
    prompt_template = PromptTemplate(template=prompt, input_variables=['query'])
    chain = prompt_template | llm | JsonOutputParser()
    result: list[str] = await chain.ainvoke({"query": query}) # type: ignore
    keywords = list(set((keywords or [] )+result))

    print("metric_recall 基础版关键词 + 大模型拓展后的关键词:", keywords)
    
    # 召回字段
    recalled_metrics_mapping: dict[str, MetricInfo] = {}
    for keyword in keywords:
            # 1. 把关键词转成向量（embedding）
        embedding = await embedding_client.client.aembed_query(keyword)
            # 2. 拿向量去 Qdrant 里做相似度搜索，返回最相关的字段列表
        metric_infos: list[MetricInfo] = await metric_qdrant_repo.search(embedding)
            #3.去重收集结果，多个关键词可能搜出同一个字段
        for metric_info in metric_infos:
            if metric_info.id not in recalled_metrics_mapping:
                recalled_metrics_mapping[metric_info.id] = metric_info
    
    recalled_metrics: list[MetricInfo] = list(recalled_metrics_mapping.values())

    print("recalled_metrics:", recalled_metrics)
    writer(WSStepInfo(step="召回指标信息", status="success"))
    return {"recalled_metrics": recalled_metrics}