"""归因分析接口:结果卡上点「归因分析」按钮时调用(SSE,协议与问数一致)。

POST /agent/attribution
  Body: {rows, query, sql, conversation_id?, datasource_id?/database?(db 页) | dataset_id?(数据集页)}
  → SSE 流:解析归因目标 → 确认现象/规划维度(并行) → 维度拆解(并发) → 综合归因。
  挂在 /agent 前缀下,直接复用前端 dev 代理的 ^/agent 规则。

归因 agent 不懂 SQL/DuckDB:此处按来源注入查询能力(run_query)与领域描述(domain_md);
当前轮的 问题/SQL/结果行 作为种子传入,归因目标直接从结果里识别,无需口径澄清。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain.messages import HumanMessage
from pydantic import BaseModel

from agent.attribution_agent import attribution_graph
from agent.attribution_agent.adapters import make_dataset_run_query, make_db_run_query
from agent.attribution_agent.schemas import AttributionContext, AttributionState
from agent.common.history import stream_with_history
from api.deps import get_current_user, get_current_username, require_owned_dataset
from clients.langfuse import build_run_config
from core.log import logger
from core.rate_limit import llm_rate_limiter

router = APIRouter(prefix="/agent")


class AttributionBody(BaseModel):
    rows: list[dict] = []
    query: str = ""
    sql: str | None = None
    conversation_id: int | None = None
    # db 页来源
    datasource_id: str | None = None
    database: str | None = None
    # 数据集页来源
    dataset_id: int | None = None


async def _make_inputs(body: AttributionBody, user_id: str) -> tuple:
    """按来源构造 (run_query, domain_md)。"""
    if body.dataset_id is not None:
        from services.dataset_loader import get_dataset_info, render_schema_for_prompt
        info = await get_dataset_info(body.dataset_id)
        domain = render_schema_for_prompt(info["schema"] or {}) if info else ""
        return make_dataset_run_query(user_id, body.dataset_id), domain

    # db 来源:构造作用域化的 WSAgentContext(与 /agent/query 同款)
    from agent.db_agent.nodes.parse_query_intention import _render_domain
    from agent.schemas import WSAgentContext
    from clients.es import es_client
    from clients.mysql import meta_mysql_client
    from clients.qdrant import qdrant_client
    from repositories.es import ESRepository
    from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository

    qc = WSAgentContext(
        meta_db_client=meta_mysql_client,
        es_repo=ESRepository(es_client.client),
        column_qdrant_repo=ColumnQdrantRepository(qdrant_client.client),
        metric_qdrant_repo=MetricQdrantRepository(qdrant_client.client),
        datasource_id=body.datasource_id or "ds_default",
        database=body.database,
    )
    async with qc.meta_repo() as repo:
        tables = await repo.get_all_tables()
        metrics = await repo.get_all_metrics()
    return make_db_run_query(qc), _render_domain(tables, metrics)


async def _chunks(body: AttributionBody, user_id: str, user_name: str | None,
                  request_id: str | None):
    run_query, domain_md = await _make_inputs(body, user_id)
    state = AttributionState(
        messages=[HumanMessage(content=f"归因分析:{body.query}")],
        seed_question=body.query, seed_sql=body.sql, seed_rows=body.rows,
    )
    ctx = AttributionContext(run_query=run_query, domain_md=domain_md)
    run_config = build_run_config(
        "attribution", user_id=user_id, user_name=user_name,
        session_id=body.conversation_id, request_id=request_id, query=body.query,
    )
    async for _ns, chunk in attribution_graph.astream(
        input=state, context=ctx, config=run_config,
        stream_mode="custom", subgraphs=True,
    ):
        yield chunk


@router.post("/attribution")
async def run_attribution(body: AttributionBody, request: Request,
                          user_id: str = Depends(get_current_user),
                          user_name: str | None = Depends(get_current_username)):
    if not body.rows:
        raise HTTPException(status_code=400, detail="没有可归因的数据")
    if body.dataset_id is not None:
        await require_owned_dataset(body.dataset_id, user_id)
    request_id = getattr(request.state, "request_id", None)
    logger.info(f"[/agent/attribution] user_id={user_id} query={body.query!r}")
    # 按用户限流:整次归因(含全部内部子查询)占 1 个并发槽,流结束自动释放
    llm_rate_limiter.acquire(user_id)
    return StreamingResponse(
        llm_rate_limiter.stream(user_id, stream_with_history(
            _chunks(body, user_id, user_name, request_id),
            user_id=user_id,
            source="dataset" if body.dataset_id is not None else "db",
            query=f"归因分析:{body.query}",
            conversation_id=body.conversation_id,
            dataset_id=body.dataset_id,
            datasource_id=body.datasource_id,
        )),
        media_type="text/event-stream",
    )
