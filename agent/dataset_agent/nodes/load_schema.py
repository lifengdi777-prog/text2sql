"""加载数据集 schema 节点:
  从 MySQL 拉 schema_json → 渲染成 markdown → 写进 state.rendered_schema。
"""
from langgraph.runtime import Runtime

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.schemas import WSStepInfo
from core.log import logger
from services.dataset_loader import get_dataset_info, render_schema_for_prompt


async def load_schema(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="加载数据集结构", status="running"))

    if state.dataset_id is None:
        msg = "缺少 dataset_id"
        writer(WSStepInfo(step="加载数据集结构", status="error", data={"error": msg}))
        return {"error": msg}

    info = await get_dataset_info(state.dataset_id)
    if info is None:
        msg = f"数据集 {state.dataset_id} 不存在"
        writer(WSStepInfo(step="加载数据集结构", status="error", data={"error": msg}))
        return {"error": msg}
    if info["status"] != "ready":
        msg = f"数据集 {state.dataset_id} 当前状态={info['status']},不可查询"
        writer(WSStepInfo(step="加载数据集结构", status="error", data={"error": msg}))
        return {"error": msg}

    rendered = render_schema_for_prompt(info["schema"] or {})
    logger.info(f"数据集 {state.dataset_id} schema 加载完成({len(rendered)} 字符)")
    writer(WSStepInfo(
        step="加载数据集结构",
        status="success",
        data={"dataset_id": state.dataset_id, "name": info["name"]},
    ))
    return {"rendered_schema": rendered}
