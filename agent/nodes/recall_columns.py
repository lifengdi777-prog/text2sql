from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from agent.llm import llm
from pydantic import BaseModel
from agent.prompts import load_prompt
from clients.embedding import embedding_client
from dtos.meta import ColumnInfo
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser

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
    chain = prompt_template | llm | JsonOutputParser()
    #这一步是真正执行整条链，而且是异步执行。
    result: list[str] = await chain.ainvoke({"query": query}) # type: ignore
    keywords = list(set((keywords or [] )+result))

    print("基础版关键词 + 大模型拓展后的关键词:", keywords)
    
    # 召回字段
    #str就是column_info.id，ColumnInfo是从qdrant里搜出来的字段信息对象
    recalled_columns_mapping: dict[str, ColumnInfo] = {}
    for keyword in keywords:
         # 1. 把关键词转成向量（embedding）
        embedding = await embedding_client.client.aembed_query(keyword)
         # 2. 拿向量去 Qdrant 里做相似度搜索，返回最相关的字段列表
        column_infos: list[ColumnInfo] = await column_qdrant_repo.search(embedding)
        #3.去重收集结果，多个关键词可能搜出同一个字段
        for column_info in column_infos:
            #用 id 判断，已存在就跳过，避免重复
            if column_info.id not in recalled_columns_mapping:
                recalled_columns_mapping[column_info.id] = column_info
    #第四步：转成列表输出
    #返回字典所有的 Value（值），忽略 Key。
    #这里就是只返回所有的 ColumnInfo 对象
    recalled_columns: list[ColumnInfo] = list(recalled_columns_mapping.values())

    print("recalled_columns:", recalled_columns)
    writer(WSStepInfo(step="召回字段信息", status="success"))
    return {"recalled_columns": recalled_columns}