import asyncio

from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.prompts import load_prompt
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from agent.llm import fast_llm
from dtos.meta import MetricInfo
from clients.embedding import embedding_client


async def recall_metrics(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="召回指标信息", status="running"))

    query = state.messages[-1].content

    context = runtime.context

    keywords = state.keywords

    metric_qdrant_repo = context.metric_qdrant_repo

    # 先使用大模型拓展关键词
    prompt = await load_prompt("extend_keywords_for_metric_recall")
    prompt_template = PromptTemplate(template=prompt, input_variables=['query'])
    chain = prompt_template | fast_llm | JsonOutputParser()
    result: list[str] = await chain.ainvoke({"query": query}) # type: ignore
    keywords = list(set((keywords or [] )+result))

    print("metric_recall 基础版关键词 + 大模型拓展后的关键词:", keywords)
    
    # 召回指标：批量 embedding（一次 API 往返）+ 并行向量检索（asyncio.gather）
    recalled_metrics_mapping: dict[str, MetricInfo] = {}
    if keywords:
        # 1. 批量把所有关键词转成向量（自动按服务端上限分批 + 并行）
        embeddings = await embedding_client.aembed_documents_batched(keywords)
        # 2. 所有向量的 Qdrant 检索并行发出
        search_results: list[list[MetricInfo]] = await asyncio.gather(*[
            metric_qdrant_repo.search(embedding) for embedding in embeddings
        ])
        # 3. 去重收集，多个关键词可能搜出同一个指标，按 id 去重
        for metric_infos in search_results:
            for metric_info in metric_infos:
                recalled_metrics_mapping.setdefault(metric_info.id, metric_info)

    recalled_metrics: list[MetricInfo] = list(recalled_metrics_mapping.values())

    # print("recalled_metrics:", recalled_metrics)
    writer(WSStepInfo(step="召回指标信息", status="success"))
    return {"recalled_metrics": recalled_metrics}