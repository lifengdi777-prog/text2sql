from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from api.schemas import QueryInput
from api.deps import get_current_user
from core.log import logger
from agent.common.history import stream_with_history
from agent.db_agent.graph import graph
from clients.mysql import dw_mysql_client, meta_mysql_client
from clients.es import es_client
from clients.qdrant import qdrant_client
from clients.embedding import embedding_client

from repositories.es import ESRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from langchain.messages import HumanMessage
from agent.schemas import WSAgentState, WSAgentContext


router = APIRouter(prefix="/agent")


async def query_graph(query: str):
    # 产出 WSStepInfo chunk(不再自己格式化 SSE);由 stream_with_history 统一转发 + 落库
    # 注意:这里不再开"贯穿整条流"的 MySQL 会话(那会让一条查询全程占用 2 个连接、严重压并发)。
    # 改为把 MySQL 客户端(连接池工厂)注入 context,各节点用 context.meta_repo()/dw_repo()
    # 只在真正查库时开短会话、用完即还,LLM 等待期间不占用任何连接。
    # ES / Qdrant 客户端本身是连接池化的 HTTP 客户端,直接复用单例即可。
    es_repo = ESRepository(es_client.client)
    column_qdrant_repo = ColumnQdrantRepository(qdrant_client.client)
    metric_qdrant_repo = MetricQdrantRepository(qdrant_client.client)

    state = WSAgentState(messages=[HumanMessage(query)])
    context = WSAgentContext(
        dw_db_client=dw_mysql_client,
        meta_db_client=meta_mysql_client,
        es_repo=es_repo,
        column_qdrant_repo=column_qdrant_repo,
        metric_qdrant_repo=metric_qdrant_repo,
    )
    #异步流式执行整个 LangGraph 图，每当有数据产出时，就立刻拿到一个 chunk（数据块）
    # subgraphs=True:让子图(如 chart_agent)节点内部 runtime.stream_writer
    # 写出的事件也能冒泡到这里,前端才能收到"分析数据形状/图表决策/生成图表"
    # 这 3 个步骤事件。返回值变成 (namespace, chunk) tuple,namespace 我们不用。
    async for namespace, chunk in graph.astream(
        input=state,
        context=context,
        stream_mode="custom",
        subgraphs=True,
    ):
        yield chunk

@router.post("/query")
async def query_data(data: QueryInput, user_id: str = Depends(get_current_user)):
    # 只加「认证」(必须是登录用户),不加「归属校验」:
    # MySQL 数仓是全员共享的同一份数据,无「谁的数据」之分,所有登录用户看到的内容一致。
    # 未登录 / token 失效 → get_current_user 抛 401,前端 axios 拦截器会自动跳登录页。
    query = data.query
    logger.info(f"[/agent/query] user_id={user_id} query={query!r}")  # 审计:记录谁问了什么
    # 用 stream_with_history 包一层:落库会话历史(归属当前用户),并回传 conversation_id
    return StreamingResponse(
        stream_with_history(
            query_graph(query),
            user_id=user_id,
            source="db",
            query=query,
            conversation_id=data.conversation_id,
        ),
        media_type="text/event-stream",
    )