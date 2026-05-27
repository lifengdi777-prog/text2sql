import asyncio

from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.prompts import load_prompt
from langchain_core.prompts import PromptTemplate
from agent.llm import llm
from langchain_core.output_parsers import JsonOutputParser
from dtos.es import ValueInfo


async def recall_values(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="召回业务数据库数据", status="running"))

    query = state.messages[-1].content
    keywords = state.keywords
    es_repo = runtime.context.es_repo

    # 先使用大模型拓展关键词
    prompt = await load_prompt("extend_keywords_for_value_recall")
    prompt_template = PromptTemplate(template=prompt, input_variables=['query'])
    chain = prompt_template | llm | JsonOutputParser()
    result: list[str] = await chain.ainvoke({"query": query}) # type: ignore
    keywords = list(set((keywords or [] )+result))

    print("value_recall 基础版关键词 + 大模型拓展后的关键词:", keywords)
    
    # 召回值：ES 文本检索本身不需要 embedding，所有关键词的 search 并行发出
    recalled_values_mapping: dict[str, ValueInfo] = {}
    if keywords:
        search_results: list[list[ValueInfo]] = await asyncio.gather(*[
            es_repo.search(keyword) for keyword in keywords
        ])
        # 去重收集，多个关键词可能搜出同一个值，按 id 去重
        for value_infos in search_results:
            for value_info in value_infos:
                recalled_values_mapping.setdefault(value_info.id, value_info)

    recalled_values: list[ValueInfo] = list(recalled_values_mapping.values())

    # print("recalled_values:", recalled_values)
    writer(WSStepInfo(step="召回业务数据库数据", status="success"))
    return {"recalled_values": recalled_values}