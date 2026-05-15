from langgraph.graph import StateGraph, START, END
from agent.schemas import WSAgentState, WSAgentContext
from agent.nodes.parse_query_intention import parse_query_intention
from agent.nodes.extract_keywords import extract_keywords
from agent.nodes.recall_columns import recall_columns
from agent.nodes.recall_metrics import recall_metrics
from agent.nodes.recall_values import recall_values
from agent.nodes.merge_recalled_infos import merge_recalled_infos
from agent.nodes.add_extra_context import add_extra_context
from agent.nodes.extract_keywords import extract_keywords
from agent.nodes.filter_metrics import filter_metrics
from agent.nodes.filter_tables import filter_tables
from agent.nodes.validate_sql import validate_sql
from agent.nodes.generate_sql import generate_sql
from agent.nodes.correct_sql import correct_sql
from agent.nodes.execute_sql import execute_sql
from langchain.messages import HumanMessage
from clients.mysql import dw_mysql_client, meta_mysql_client
from clients.es import es_client
from clients.qdrant import qdrant_client
from clients.embedding import embedding_client
from repositories.mysql import DWDBRepository, MetaDBRepository
from repositories.es import ESRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository

#定义图的结构（StateGraph）
#state_schema=WSAgentState表示整个图运行时共享的状态结构。
#context_schema=WSAgentContext表示整个图运行时共享的上下文结构。
graph_builder = StateGraph(state_schema=WSAgentState, context_schema=WSAgentContext)
#添加节点
graph_builder.add_node(parse_query_intention)
graph_builder.add_node(extract_keywords)
graph_builder.add_node(recall_columns)
graph_builder.add_node(recall_metrics)
graph_builder.add_node(recall_values)
graph_builder.add_node(merge_recalled_infos)
graph_builder.add_node(filter_metrics)
graph_builder.add_node(filter_tables)
graph_builder.add_node(add_extra_context)
graph_builder.add_node(generate_sql)
graph_builder.add_node(validate_sql)
graph_builder.add_node(correct_sql)
graph_builder.add_node(execute_sql)

#添加边
graph_builder.add_edge(START, "parse_query_intention")
#根据当前 state 的内容，决定下一步走哪个节点。
def judge(state: WSAgentState):
    print(f"should_continue: {state.should_continue}")
    #如果should_continue为True，继续执行extract_keywords节点；如果为False，结束流程。
    if state.should_continue:
        return "extract_keywords"
    #返回end节点
    return END

#add_conditional_edges —— 条件边，允许我们根据当前状态动态地决定流程走向。
graph_builder.add_conditional_edges(
    "parse_query_intention", # 从哪个节点出发
    judge,                     # 用哪个函数来判断走哪条路
    {"extract_keywords": "extract_keywords", END: END}# 路由映射表
    #{ 路由函数的返回值: 实际节点名 }
)
#
graph_builder.add_edge("extract_keywords", "recall_columns")
graph_builder.add_edge("extract_keywords", "recall_metrics")
graph_builder.add_edge("extract_keywords", "recall_values")
#把召回的信息合并到一起，方便后续处理
graph_builder.add_edge("recall_values", "merge_recalled_infos")
graph_builder.add_edge("recall_metrics", "merge_recalled_infos")
graph_builder.add_edge("recall_values", "merge_recalled_infos")
#根据召回的信息，过滤掉不相关的指标和表
graph_builder.add_edge("merge_recalled_infos", "filter_metrics")
graph_builder.add_edge("merge_recalled_infos", "filter_tables")
#根据过滤的结果，添加额外的上下文信息，帮助生成更准确的SQL
graph_builder.add_edge("filter_metrics", "add_extra_context")
graph_builder.add_edge("filter_tables", "add_extra_context")
#根据之前的上下文信息生成SQL语句。
graph_builder.add_edge("add_extra_context", "generate_sql")
#校验生成的SQL是否正确，是否符合规范。
graph_builder.add_edge("generate_sql", "validate_sql")
#如果SQL有问题，进入校正流程；如果没问题，直接执行。
graph_builder.add_conditional_edges(
    "validate_sql", 
    #根据state中的error字段来判断是否需要校正。如果error不为None，说明SQL有问题，需要校正；
    #如果error为None，说明SQL没问题，可以执行了。
    lambda state: "correct_sql" if state.error else "execute_sql"
)
#如果需要校正，校正完后继续执行SQL。
graph_builder.add_edge("correct_sql", "validate_sql")
#最后都要进入END节点，结束整个流程。
graph_builder.add_edge("execute_sql", END)
#编译图，生成最终的可执行图对象。
graph = graph_builder.compile()


if __name__ == "__main__":
    import asyncio
    async def main():
        query = "帮我统计一下上个季度上海市的华东地区销售额排名前三的产品"
        try:
            async with (
                dw_mysql_client.session() as dw_session,
                meta_mysql_client.session() as meta_session
            ):  
                dw_db_repo = DWDBRepository(dw_session)
                meta_db_repo = MetaDBRepository(meta_session)
                es_repo = ESRepository(es_client.client)
                column_qdrant_repo = ColumnQdrantRepository(qdrant_client.client)
                metric_qdrant_repo = MetricQdrantRepository(qdrant_client.client)
                state = WSAgentState(messages=[HumanMessage(query)])
                context = WSAgentContext(
                    dw_db_repo=dw_db_repo,
                    meta_db_repo=meta_db_repo,
                    es_repo=es_repo,
                    column_qdrant_repo=column_qdrant_repo,
                    metric_qdrant_repo=metric_qdrant_repo
                )
                #异步流式执行整个 LangGraph 图，每当有数据产出时，就立刻拿到一个 chunk（数据块）
                async for chunk in graph.astream(
                    input=state,           # 初始状态 对应 state_schema=WSAgentState
                    context=context,       # 上下文（各种 repo 客户端） 对应 context_schema=WSAgentContext
                    stream_mode="custom"   # 流式模式 "custom" 表示我们自己控制流式输出，而不是默认的按节点输出
                ):
                    print(chunk)
        finally:
            await dw_mysql_client.close()
            await meta_mysql_client.close()
            await qdrant_client.close()
            await es_client.close()
    asyncio.run(main())