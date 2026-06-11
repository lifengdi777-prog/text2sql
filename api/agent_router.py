from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from api.schemas import QueryInput
from api.deps import get_current_user, get_current_username
from core.log import logger
from core.rate_limit import llm_rate_limiter
from agent.common.history import stream_with_history
from clients.langfuse import build_run_config
from agent.supervisor.graph import db_supervisor
from agent.supervisor.schemas import SupervisorContext, SupervisorState
from clients.mysql import meta_mysql_client
from clients.es import es_client
from clients.qdrant import qdrant_client
from clients.embedding import embedding_client

from repositories.es import ESRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from repositories.conversation import ConversationRepository
from repositories.datasource import DatasourceRepository
from repositories.sql_cache import SqlCacheRepository
from services.excel_ingest import get_session_factory
from langchain.messages import HumanMessage
from agent.schemas import WSAgentContext


router = APIRouter(prefix="/agent")


@router.get("/hot-questions")
async def hot_questions(datasource_id: str = "ds_default", limit: int = 6,
                        user_id: str = Depends(get_current_user)):
    """问数页空状态的「历史热门问题」:本数据源当前版本下命中最多的缓存问题。

    点选后逐字提问 → 必然精确命中 SQL 缓存,秒出结果(行为层提升缓存命中率)。
    任何异常都返回空列表:这是锦上添花的区块,绝不影响问数主流程。
    """
    limit = max(1, min(limit, 12))
    try:
        async with meta_mysql_client.session() as session:
            ds = await DatasourceRepository(session).get_by_id(datasource_id)
            meta_version = (ds.meta_version if ds else 1) or 1
            questions = await SqlCacheRepository(session).top_questions(
                datasource_id, meta_version, limit)
        return {"questions": questions}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"取热门问题失败(前端将不显示该区块):{exc}")
        return {"questions": []}


async def query_graph(query: str, user_id: str | None = None, user_name: str | None = None,
                      request_id: str | None = None, session_id: int | None = None,
                      datasource_id: str = "ds_default", database: str | None = None):
    # 产出 WSStepInfo chunk(不再自己格式化 SSE);由 stream_with_history 统一转发 + 落库
    # 注意:这里不再开"贯穿整条流"的 MySQL 会话(那会让一条查询全程占用 2 个连接、严重压并发)。
    # 改为把 MySQL 客户端(连接池工厂)注入 context,各节点用 context.meta_repo()/dw_repo()
    # 只在真正查库时开短会话、用完即还,LLM 等待期间不占用任何连接。
    # ES / Qdrant 客户端本身是连接池化的 HTTP 客户端,直接复用单例即可。
    es_repo = ESRepository(es_client.client)
    column_qdrant_repo = ColumnQdrantRepository(qdrant_client.client)
    metric_qdrant_repo = MetricQdrantRepository(qdrant_client.client)

    # 多轮:有会话才加载历史(最近几轮的 问题/SQL/结果前 N 行),供 parse_query_intention 指代消解。
    # 用一个短会话读完即还;此刻当前 user 消息已落库但还没 assistant 回复,会被配对逻辑排除。
    history: list[dict] = []
    if session_id is not None:
        Session = get_session_factory()
        async with Session() as s:
            history = await ConversationRepository(s).load_recent_turns(session_id)

    # 入口改接 supervisor 父图:route_intent 先分流「画图 / 查询」,
    # 查询走 db_agent(context 原样透传),画图走 chart_agent(按 conversation_id 自取历史结果)。
    # 新会话(session_id=None)首句让画图 → chart_agent 取不到数,发"请先查询数据"说明卡,行为正确。
    state = SupervisorState(messages=[HumanMessage(query)], history=history)
    context = SupervisorContext(
        query_context=WSAgentContext(
            meta_db_client=meta_mysql_client,
            es_repo=es_repo,
            column_qdrant_repo=column_qdrant_repo,
            metric_qdrant_repo=metric_qdrant_repo,
            datasource_id=datasource_id,
            database=database,
        ),
        conversation_id=session_id,
    )
    # Langfuse 追踪 + 运行元数据(未启用 Langfuse 时只带 run_name/metadata,无害)
    run_config = build_run_config(
        "db_query", user_id=user_id, user_name=user_name, session_id=session_id,
        request_id=request_id, query=query,
    )
    #异步流式执行整个 LangGraph 图，每当有数据产出时，就立刻拿到一个 chunk（数据块）
    # subgraphs=True:supervisor 包装节点里 ainvoke 的子图(db_agent / chart_agent)
    # 节点内部 runtime.stream_writer 写出的事件经此冒泡到这里,前端协议不变。
    # 返回值是 (namespace, chunk) tuple,namespace 我们不用。
    async for namespace, chunk in db_supervisor.astream(
        input=state,
        context=context,
        config=run_config,
        stream_mode="custom",
        subgraphs=True,
    ):
        yield chunk

@router.post("/query")
async def query_data(data: QueryInput, request: Request, user_id: str = Depends(get_current_user),
                     user_name: str | None = Depends(get_current_username)):
    # 只加「认证」(必须是登录用户),不加「归属校验」:
    # MySQL 数仓是全员共享的同一份数据,无「谁的数据」之分,所有登录用户看到的内容一致。
    # 未登录 / token 失效 → get_current_user 抛 401,前端 axios 拦截器会自动跳登录页。
    query = data.query
    request_id = getattr(request.state, "request_id", None)
    logger.info(f"[/agent/query] user_id={user_id} query={query!r}")  # 审计:记录谁问了什么
    # 按用户限流(并发+频率),防单用户打满 LLM 配额;超限抛 429,流结束自动释放并发槽
    llm_rate_limiter.acquire(user_id)
    # 用 stream_with_history 包一层:落库会话历史(归属当前用户),并回传 conversation_id
    return StreamingResponse(
        llm_rate_limiter.stream(user_id, stream_with_history(
            query_graph(query, user_id=user_id, user_name=user_name, request_id=request_id,
                        session_id=data.conversation_id,
                        datasource_id=data.datasource_id, database=data.database),
            user_id=user_id,
            source="db",
            query=query,
            conversation_id=data.conversation_id,
            datasource_id=data.datasource_id,
        )),
        media_type="text/event-stream",
    )