from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from api.schemas import QueryInput
from agent.graph import graph
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
        async for chunk in graph.astream(
            input=state, 
            context=context, 
            stream_mode="custom"# 使用自定义流模式，由各节点的 writer 控制输出
        ):
            yield f"data: {chunk.model_dump_json()}\n\n"
#           ↑ SSE 格式：每条消息以 "data: " 开头，以 "\n\n" 结尾

@router.post("/query")
async def query_data(data: QueryInput):
    query = data.query
    return StreamingResponse(query_graph(query), media_type="text/event-stream")
    #       ↑ 把生成器包装成 SSE 流式响应返回给前端