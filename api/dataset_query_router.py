"""数据集查询接口:用户上传的 Excel 数据做问答。

POST /dataset/{dataset_id}/query
  Body: {"query": "...", "user_id": "..."}
  Returns: SSE 流(协议跟主 DW 接口完全一致,前端可同套渲染逻辑)
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain.messages import HumanMessage
from pydantic import BaseModel

from agent.dataset_agent.graph import dataset_graph
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState

router = APIRouter(prefix="/dataset")


class DatasetQueryBody(BaseModel):
    query: str
    user_id: str = "anonymous"


async def _stream(dataset_id: int, query: str, user_id: str):
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
        yield f"data: {chunk.model_dump_json()}\n\n"


@router.post("/{dataset_id}/query")
async def query_dataset(dataset_id: int, body: DatasetQueryBody):
    return StreamingResponse(
        _stream(dataset_id, body.query, body.user_id),
        media_type="text/event-stream",
    )
