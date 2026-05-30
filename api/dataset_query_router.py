"""数据集查询接口:用户上传的 Excel 数据做问答。

POST /dataset/{dataset_id}/query
  Body: {"query": "...", "user_id": "..."}
  Returns: SSE 流(协议跟主 DW 接口完全一致,前端可同套渲染逻辑)
"""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain.messages import HumanMessage
from pydantic import BaseModel

from agent.common.history import stream_with_history
from agent.dataset_agent.graph import dataset_graph
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from api.deps import get_current_user, require_owned_dataset

router = APIRouter(prefix="/dataset")


class DatasetQueryBody(BaseModel):
    query: str
    # 可选:续聊到已有会话;不传则后端新建并回传 conversation_id
    conversation_id: int | None = None


async def _graph_chunks(dataset_id: int, query: str, user_id: str):
    # 产出 WSStepInfo chunk;SSE 格式化 + 历史落库交给 stream_with_history
    state = DatasetAgentState(
        messages=[HumanMessage(content=query)],
        dataset_id=dataset_id,
    )
    context = DatasetAgentContext(user_id=user_id)
    # subgraphs=True:让 chart_subgraph 内部的 stream_writer 事件冒泡上来
    async for namespace, chunk in dataset_graph.astream(
        input=state,
        context=context,
        stream_mode="custom",
        subgraphs=True,
    ):
        yield chunk


@router.post("/{dataset_id}/query")
async def query_dataset(
    dataset_id: int,
    body: DatasetQueryBody,
    user_id: str = Depends(get_current_user),
):
    # 先校验归属:不属于当前用户(或不存在)→ 404,绝不进入流式计算管线
    await require_owned_dataset(dataset_id, user_id)
    return StreamingResponse(
        stream_with_history(
            _graph_chunks(dataset_id, body.query, user_id),
            user_id=user_id,
            source="dataset",
            query=body.query,
            conversation_id=body.conversation_id,
            dataset_id=dataset_id,
        ),
        media_type="text/event-stream",
    )
