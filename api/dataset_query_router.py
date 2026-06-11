"""数据集查询接口:用户上传的 Excel 数据做问答。

POST /dataset/{dataset_id}/query
  Body: {"query": "...", "user_id": "..."}
  Returns: SSE 流(协议跟主 DW 接口完全一致,前端可同套渲染逻辑)
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain.messages import HumanMessage
from pydantic import BaseModel

from agent.common.history import stream_with_history
from agent.dataset_agent.schemas import DatasetAgentContext
from agent.supervisor.graph import dataset_supervisor
from agent.supervisor.schemas import SupervisorContext, SupervisorState
from api.deps import get_current_user, get_current_username, require_owned_dataset
from clients.langfuse import build_run_config

router = APIRouter(prefix="/dataset")


class DatasetQueryBody(BaseModel):
    query: str
    # 可选:续聊到已有会话;不传则后端新建并回传 conversation_id
    conversation_id: int | None = None


async def _graph_chunks(dataset_id: int, query: str, user_id: str, user_name: str | None = None,
                        request_id: str | None = None, session_id: int | None = None):
    # 产出 WSStepInfo chunk;SSE 格式化 + 历史落库交给 stream_with_history
    # 多轮:有会话则加载最近几轮(question+sql+结果快照)注入 history,供 parse_intent 做指代消解。
    # 当前这轮的 user 消息此刻虽已落库,但还没 assistant 回复 → load_recent_turns 自动排除,不会自指。
    history: list[dict] = []
    if session_id is not None:
        from repositories.conversation import ConversationRepository
        from services.excel_ingest import get_session_factory
        async with get_session_factory()() as s:
            history = await ConversationRepository(s).load_recent_turns(session_id)
    # 入口改接 supervisor 父图:route_intent 先分流「画图 / 查询」,
    # 查询走 dataset_agent(context 原样透传),画图走 chart_agent(按 conversation_id 自取历史结果)。
    state = SupervisorState(
        messages=[HumanMessage(content=query)],
        dataset_id=dataset_id,
        history=history,
    )
    context = SupervisorContext(
        query_context=DatasetAgentContext(user_id=user_id),
        conversation_id=session_id,
    )
    # Langfuse 追踪 + 运行元数据(未启用 Langfuse 时只带 run_name/metadata,无害)
    run_config = build_run_config(
        "dataset_query", user_id=user_id, user_name=user_name, session_id=session_id,
        request_id=request_id, query=query,
    )
    # subgraphs=True:supervisor 包装节点里 ainvoke 的子图(dataset_agent / chart_agent)
    # 节点内部 stream_writer 写出的事件经此冒泡上来,前端协议不变
    async for namespace, chunk in dataset_supervisor.astream(
        input=state,
        context=context,
        config=run_config,
        stream_mode="custom",
        subgraphs=True,
    ):
        yield chunk


@router.post("/{dataset_id}/query")
async def query_dataset(
    dataset_id: int,
    body: DatasetQueryBody,
    request: Request,
    user_id: str = Depends(get_current_user),
    user_name: str | None = Depends(get_current_username),
):
    # 先校验归属:不属于当前用户(或不存在)→ 404,绝不进入流式计算管线
    await require_owned_dataset(dataset_id, user_id)
    request_id = getattr(request.state, "request_id", None)
    return StreamingResponse(
        stream_with_history(
            _graph_chunks(dataset_id, body.query, user_id, user_name=user_name,
                          request_id=request_id, session_id=body.conversation_id),
            user_id=user_id,
            source="dataset",
            query=body.query,
            conversation_id=body.conversation_id,
            dataset_id=dataset_id,
        ),
        media_type="text/event-stream",
    )
