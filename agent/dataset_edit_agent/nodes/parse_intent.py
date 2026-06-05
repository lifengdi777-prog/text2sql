"""意图识别节点:edit / query / chitchat 三分流(前置,只用结构、不物化)。

闲聊 / 查询 → 发引导卡(finish)并 should_continue=False(图路由到 END);
编辑 → should_continue=True,继续生成 SQL。
"""
from langgraph.runtime import Runtime

from agent.dataset_edit_agent.nodes import latest_user_query
from agent.dataset_edit_agent.nodes._common import (GUIDE_CHITCHAT, GUIDE_QUERY,
                                                    classify_intent, schema_brief)
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState
from agent.schemas import WSStepInfo
from services.dataset_loader import get_dataset_info


async def parse_intent(state: DatasetEditState, runtime: Runtime[DatasetEditContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="意图识别", status="running"))

    info = await get_dataset_info(state.dataset_id)
    if info is None or info.get("status") != "ready":
        writer(WSStepInfo(step="意图识别", status="error",
                          data={"error": "数据集不存在或不可用"}, finish=True))
        return {"should_continue": False, "error": "数据集不存在或不可用"}

    data_sheets = list((info.get("schema") or {}).get("sheets", {}).keys())
    instruction = latest_user_query(state.messages)
    intent = await classify_intent(instruction, schema_brief(info))

    if intent.kind == "query":
        writer(WSStepInfo(step="意图识别", status="success", finish=True,
                          data={"guidance": intent.reply or GUIDE_QUERY}))
        return {"intent_kind": "query", "should_continue": False}
    if intent.kind == "chitchat":
        writer(WSStepInfo(step="意图识别", status="success", finish=True,
                          data={"guidance": intent.reply or GUIDE_CHITCHAT}))
        return {"intent_kind": "chitchat", "should_continue": False}

    writer(WSStepInfo(step="意图识别", status="success"))
    return {"intent_kind": "edit", "should_continue": True, "data_sheets": data_sheets}
