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

from repositories.mysql import DWDBRepository, MetaDBRepository
from repositories.es import ESRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from langchain.messages import HumanMessage
from agent.schemas import WSAgentState, WSAgentContext


router = APIRouter(prefix="/agent")


async def query_graph(query: str):
    # 产出 WSStepInfo chunk(不再自己格式化 SSE);由 stream_with_history 统一转发 + 落库
    async with (
        dw_mysql_client.session() as dw_session,
        meta_mysql_client.session() as meta_session
    ):
        dw_db_repo = DWDBRepository(dw_session) # 操作数据仓库
        meta_db_repo = MetaDBRepository(meta_session)# 操作元数据库
        es_repo = ESRepository(es_client.client)# 操作ES（全文检索）
        column_qdrant_repo = ColumnQdrantRepository(qdrant_client.client)# 操作字段向量库
        metric_qdrant_repo = MetricQdrantRepository(qdrant_client.client)# 操作指标向量库
        # State：图的状态，携带用户消息
        state = WSAgentState(messages=[HumanMessage(query)])
        # Context：图的上下文，携带所有数据访问工具
    #Context 里放 Repository，本质上是在做依赖注入——把外部依赖统一在请求入口创建好，
    # 注入给图里的所有节点使用，而不是让每个节点自己去管连接的创建和释放。
        context = WSAgentContext(
            dw_db_repo=dw_db_repo,
            meta_db_repo=meta_db_repo,
            es_repo=es_repo,
            column_qdrant_repo=column_qdrant_repo,
            metric_qdrant_repo=metric_qdrant_repo
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