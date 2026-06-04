import asyncio

from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.llm import fast_llm
from pydantic import BaseModel
from agent.prompts import load_prompt
from clients.embedding import embedding_client
from dtos.meta import ColumnInfo
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from core.log import logger

#这一整个方法就是把用户的提问提取出关键词，然后用这些关键词去向量数据库里搜相关的字段信息
async def recall_columns(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="召回字段信息", status="running"))

    query = state.messages[-1].content

    context = runtime.context
    
    keywords = state.keywords

    column_qdrant_repo = context.column_qdrant_repo

    # 把用户的查询和之前的提示词模版结合起来，生成一个新的提示词，来让大模型帮我们提取出更多的关键词。
    prompt = await load_prompt("extend_keywords_for_column_recall")
    #PromptTemplate 是 langchain_core 提供的一个类，用于定义提示词模板。它接受一个字符串模板和输入变量列表，在执行时会将输入变量替换到模板中，生成最终的提示词文本。
    prompt_template = PromptTemplate(template=prompt, input_variables=['query'])
    #PromptTemplate 的作用就是在调用时，把 {query} 替换成真实的查询内容
    #llm负责调用大模型
    #JsonOutputParser()负责把大模型返回的文本解析成 JSON
    chain = prompt_template | fast_llm | JsonOutputParser()
    #这一步是真正执行整条链，而且是异步执行。
    # 关键词扩展是"锦上添花":LLM/JSON 解析失败或返回非列表时,回退到基础关键词,
    # 不让这一步拖垮整条召回(它跑挂会导致本次查询直接失败)。
    try:
        result = await chain.ainvoke({"query": query})  # type: ignore
        expanded = [w for w in result if isinstance(w, str)] if isinstance(result, list) else []
        if not isinstance(result, list):
            logger.warning(f"column_recall 关键词扩展返回非列表,已忽略:{type(result).__name__}")
    except Exception as exc:
        expanded = []
        logger.warning(f"column_recall 关键词扩展失败,回退基础关键词:{exc}")
    keywords = list(set((keywords or []) + expanded))

    print("column_recall 基础版关键词 + 大模型拓展后的关键词:", keywords)
    
    # 召回字段：批量 embedding（一次 API 往返）+ 并行向量检索（asyncio.gather）
    recalled_columns_mapping: dict[str, ColumnInfo] = {}
    if keywords:
        # 1. 批量把所有关键词转成向量（自动按服务端上限分批 + 并行）
        embeddings = await embedding_client.aembed_documents_batched(keywords)
        # 2. 所有向量的 Qdrant 检索并行发出，而不是排队串行
        search_results: list[list[ColumnInfo]] = await asyncio.gather(*[
            column_qdrant_repo.search(embedding, context.datasource_id) for embedding in embeddings
        ])
        # 3. 去重收集，多个关键词可能搜出同一个字段，按 id 去重
        for column_infos in search_results:
            for column_info in column_infos:
                recalled_columns_mapping.setdefault(column_info.id, column_info)
    recalled_columns: list[ColumnInfo] = list(recalled_columns_mapping.values())

    # print("recalled_columns:", recalled_columns)
    writer(WSStepInfo(step="召回字段信息", status="success"))
    return {"recalled_columns": recalled_columns}