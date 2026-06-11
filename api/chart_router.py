"""按需图表接口:前端拿到问数结果后,用户点「生成图表」才调这里出图。

图表不再在问数执行链里自动生成(db_agent / dataset_agent 已去掉 generate_chart 节点),
改为前端按需调用,省掉每次都跑的图表生成开销。

POST /chart  Body: {rows: [...], query: "..."}  → 返回 chart_config(同子图产出结构)
"""
from fastapi import APIRouter, Depends
from langchain.messages import HumanMessage
from pydantic import BaseModel

from agent.chart_agent import chart_subgraph
from agent.chart_agent.schemas import ChartAgentState
from api.deps import get_current_user
from core.log import logger
from core.rate_limit import llm_rate_limiter

router = APIRouter()


class ChartBody(BaseModel):
    rows: list[dict] = []
    query: str = ""


@router.post("/chart")
async def generate_chart(body: ChartBody, user_id: str = Depends(get_current_user)):
    """对给定结果行生成 ECharts 配置。复用 chart_subgraph(它只读 sql_result/messages,不读 context)。"""
    state = ChartAgentState(messages=[HumanMessage(content=body.query)], sql_result=body.rows)
    # 按用户限流(与 SSE 端点共享同一套配额):图表的 LLM 选型调用也计入,超限抛 429
    async with llm_rate_limiter.slot(user_id):
        try:
            # chart_subgraph 节点只用 stream_writer + state,不读 context;非流式 ainvoke 直接取末态 chart_config
            final = await chart_subgraph.ainvoke(state, context=None)
            return {"chart_config": (final or {}).get("chart_config")}
        except Exception as exc:
            logger.exception(f"按需生成图表失败:{exc}")
            return {"chart_config": None, "error": "图表生成失败,请稍后重试"}
