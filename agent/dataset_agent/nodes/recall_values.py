"""ES 值召回节点:
  把用户问题整段丢给 ES match(ik_max_word 分词),搜该 dataset 的真实值。
  命中的 (sheet, col, value) 元组写进 state.value_hits,给 LLM 写 spec 时参考。

MVP 不调 LLM 扩词:直接用用户原 query → ES tokenize → 检索。
准确率不够再加 LLM 扩词层。
"""
from langgraph.runtime import Runtime

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.schemas import WSStepInfo
from clients.es import es_client
from core.log import logger
from repositories.es import UploadESRepository

# 单次 ES 召回上限(避免噪音值过多吞掉 prompt 篇幅)
_HIT_LIMIT = 30


async def recall_values(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="召回相关值", status="running"))

    if state.dataset_id is None:
        return {"value_hits": []}

    query = state.messages[0].content if state.messages else ""
    if not query:
        writer(WSStepInfo(step="召回相关值", status="success", data={"hits": 0}))
        return {"value_hits": []}

    try:
        repo = UploadESRepository(es_client.client)
        hits = await repo.search_values(
            dataset_id=state.dataset_id,
            keyword=str(query),
            limit=_HIT_LIMIT,
        )
        logger.info(f"数据集 {state.dataset_id} 召回 {len(hits)} 个值")
        writer(WSStepInfo(
            step="召回相关值",
            status="success",
            data={"hits": len(hits), "sample": hits[:5]},
        ))
        return {"value_hits": hits}
    except Exception as exc:
        # ES 召回不可用不阻断主流程,LLM 仍可看 schema 写 spec
        logger.warning(f"ES 值召回失败,继续流程:{exc}")
        writer(WSStepInfo(
            step="召回相关值",
            status="error",
            data={"error": str(exc), "fallback": "continue without value hints"},
        ))
        return {"value_hits": []}
