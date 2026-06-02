from langgraph.graph import StateGraph, START, END
from agent.schemas import WSAgentState, WSAgentContext
from core.log import logger
from agent.db_agent.nodes.parse_query_intention import parse_query_intention
from agent.db_agent.nodes.extract_keywords import extract_keywords
from agent.db_agent.nodes.recall_columns import recall_columns
from agent.db_agent.nodes.recall_metrics import recall_metrics
from agent.db_agent.nodes.recall_values import recall_values
from agent.db_agent.nodes.merge_recalled_infos import merge_recalled_infos
from agent.db_agent.nodes.add_extra_context import add_extra_context
from agent.db_agent.nodes.extract_keywords import extract_keywords
from agent.db_agent.nodes.filter_metrics import filter_metrics
from agent.db_agent.nodes.filter_tables import filter_tables
from agent.db_agent.nodes.complete_join_path import complete_join_path
from agent.db_agent.nodes.detect_fanout import detect_fanout
from agent.db_agent.nodes.validate_sql import validate_sql
from agent.db_agent.nodes.generate_sql import generate_sql
from agent.db_agent.nodes.correct_sql import correct_sql
from agent.db_agent.nodes.execute_sql import execute_sql
from agent.db_agent.nodes.translate_columns import translate_columns  # 结果列名翻译(英文→中文)
from agent.common.interpret_result import interpret_result  # 数据解读节点(共享,与图表并行)
from agent.chart_agent import chart_subgraph  # 图表生成子图(以节点身份接入)
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
graph_builder.add_node(complete_join_path)
graph_builder.add_node(detect_fanout)
graph_builder.add_node(add_extra_context)
graph_builder.add_node(generate_sql)
graph_builder.add_node(validate_sql)
graph_builder.add_node(correct_sql)
graph_builder.add_node(execute_sql)
# 结果列名翻译:execute_sql 之后、图表/解读之前,把英文列 key 改成中文,
# 让图表轴名/图例/表头与数据解读全部中文且一致。SQL 本身保持英文不变。
graph_builder.add_node(translate_columns)
# chart_agent 子图作为一个节点接入主图(LangGraph 1.x subgraph 模式)
# 子图内部处理 4 种 sql_result 状态:正常多行→LLM 决策图表 / 单值→指标卡 / 空→empty / 报错→error
graph_builder.add_node("generate_chart", chart_subgraph)
# 数据解读节点：与 generate_chart 并行，二者都只依赖 sql_result
graph_builder.add_node("interpret_result", interpret_result)

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
graph_builder.add_edge("recall_columns", "merge_recalled_infos")
graph_builder.add_edge("recall_metrics", "merge_recalled_infos")
graph_builder.add_edge("recall_values", "merge_recalled_infos")
#根据召回的信息，过滤掉不相关的指标和表
graph_builder.add_edge("merge_recalled_infos", "filter_metrics")
graph_builder.add_edge("merge_recalled_infos", "filter_tables")
#根据过滤的结果，添加额外的上下文信息，帮助生成更准确的SQL
#filter_metrics 与 filter_tables 同为 merge 的并行分支，两者在 complete_join_path 汇合(屏障)：
#  必须让两条分支等长地在同一节点汇合，否则 add_extra_context 会被两条不等长路径触发两次，
#  导致 generate_sql/execute_sql 跑两遍、两个分支同时写 sql_result 而报 InvalidUpdateError。
#汇合后单链推进：complete_join_path(补回被剪掉的连接中间表) → detect_fanout(扇出检测) → add_extra_context。
graph_builder.add_edge("filter_metrics", "complete_join_path")
graph_builder.add_edge("filter_tables", "complete_join_path")
graph_builder.add_edge("complete_join_path", "detect_fanout")
graph_builder.add_edge("detect_fanout", "add_extra_context")
#汇合之后直接生成 SQL：JOIN 由 generate_sql 依据 table_infos 里的主外键描述自行连接。
graph_builder.add_edge("add_extra_context", "generate_sql")
#校验生成的SQL是否正确，是否符合规范。
graph_builder.add_edge("generate_sql", "validate_sql")

#SQL 校正的最大重试次数：超过后即便仍未通过也不再校正，避免无限循环。
MAX_CORRECT_ATTEMPTS = 3

#校验之后的路由：决定继续校正、放弃修复、还是执行。
def route_after_validate(state: WSAgentState):
    #1. 校验通过（无 error）→ 直接执行
    if not state.error:
        return "execute_sql"
    #2. 校验失败但还没到重试上限 → 继续进入校正流程
    if state.correct_attempts < MAX_CORRECT_ATTEMPTS:
        return "correct_sql"
    #3. 已达重试上限仍未修好 → 不再校正，交给 execute_sql 走它的异常分支，
    #   把真实错误以"查询失败"结果返回给用户，避免在校验↔校正间无限循环撞 recursion_limit。
    logger.warning(f"SQL 校正已达上限 {MAX_CORRECT_ATTEMPTS} 次仍未通过，停止修复。最后错误：{state.error}")
    return "execute_sql"

#如果SQL有问题，进入校正流程；如果没问题（或已达重试上限），直接执行。
graph_builder.add_conditional_edges(
    "validate_sql",
    route_after_validate,
    {"correct_sql": "correct_sql", "execute_sql": "execute_sql"}
)
#如果需要校正，校正完后继续执行SQL。
graph_builder.add_edge("correct_sql", "validate_sql")
#execute_sql 完成后先翻译列名(英文→中文),再并行 fan-out 到两个分支：
#  - generate_chart:图表生成子图
#  - interpret_result:自然语言解读
#二者都读已翻译的 sql_result,所以图表与解读的列名一致。
graph_builder.add_edge("execute_sql", "translate_columns")
graph_builder.add_edge("translate_columns", "generate_chart")
graph_builder.add_edge("translate_columns", "interpret_result")
#两个分支各自终结到 END,LangGraph 会等两者都完成
graph_builder.add_edge("generate_chart", END)
graph_builder.add_edge("interpret_result", END)
#编译图，生成最终的可执行图对象。
graph = graph_builder.compile()


# if __name__ == "__main__":
#     import asyncio
#     async def main():
#         query = "帮我统计一下上个季度上海市的华东地区销售额排名前三的产品"
#         try:
#             async with (
#                 dw_mysql_client.session() as dw_session,
#                 meta_mysql_client.session() as meta_session
#             ):  
#                 dw_db_repo = DWDBRepository(dw_session)
#                 meta_db_repo = MetaDBRepository(meta_session)
#                 es_repo = ESRepository(es_client.client)
#                 column_qdrant_repo = ColumnQdrantRepository(qdrant_client.client)
#                 metric_qdrant_repo = MetricQdrantRepository(qdrant_client.client)
#                 state = WSAgentState(messages=[HumanMessage(query)])
#                 context = WSAgentContext(
#                     dw_db_repo=dw_db_repo,
#                     meta_db_repo=meta_db_repo,
#                     es_repo=es_repo,
#                     column_qdrant_repo=column_qdrant_repo,
#                     metric_qdrant_repo=metric_qdrant_repo
#                 )
#                 #异步流式执行整个 LangGraph 图，每当有数据产出时，就立刻拿到一个 chunk（数据块）
#                 async for chunk in graph.astream(
#                     input=state,           # 初始状态 对应 state_schema=WSAgentState
#                     context=context,       # 上下文（各种 repo 客户端） 对应 context_schema=WSAgentContext
#                     stream_mode="custom"   # 流式模式 "custom" 表示我们自己控制流式输出，而不是默认的按节点输出
#                 ):
#                     print(chunk)
#         finally:
#             await dw_mysql_client.close()
#             await meta_mysql_client.close()
#             await qdrant_client.close()
#             await es_client.close()
#     asyncio.run(main())