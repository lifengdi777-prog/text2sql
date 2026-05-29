"""执行 ComputeSpec 节点:加载对应 sheet 的 DataFrame,用 pandas 跑 spec,得到 rows。

rows 直接写进 state.sql_result(继承自 WSAgentState),
下游 chart_agent / interpret_result 原样消费,不用改一行。
"""
from langgraph.runtime import Runtime

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.schemas import WSStepInfo
from core.log import logger
from services.compute_spec import ComputeSpec
from services.compute_spec import execute_spec as _run_spec
from services.dataset_loader import load_sheet_df


async def execute_spec(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="执行计算", status="running"))

    if state.error:
        return {}
    if state.compute_spec is None:
        msg = "缺少 compute_spec(LLM 步骤未产出)"
        writer(WSStepInfo(step="执行计算", status="error", data={"error": msg}))
        return {"error": msg}
    if state.dataset_id is None:
        msg = "缺少 dataset_id"
        writer(WSStepInfo(step="执行计算", status="error", data={"error": msg}))
        return {"error": msg}

    # dict → ComputeSpec(Pydantic 实例)
    try:
        spec = ComputeSpec.model_validate(state.compute_spec)
    except Exception as exc:
        msg = f"compute_spec 不合法:{exc}"
        writer(WSStepInfo(step="执行计算", status="error", data={"error": msg}))
        return {"error": msg}

    # 加载 sheet 对应的 DataFrame(带 LRU 缓存)
    try:
        df = await load_sheet_df(state.dataset_id, spec.sheet)
    except Exception as exc:
        msg = f"加载 sheet '{spec.sheet}' 失败:{exc}"
        writer(WSStepInfo(step="执行计算", status="error", data={"error": msg}))
        return {"error": msg}

    # 跑 spec
    try:
        rows = _run_spec(df, spec)
    except Exception as exc:
        logger.exception(f"execute_spec 失败:{exc}")
        msg = f"执行计算失败:{exc}"
        writer(WSStepInfo(step="执行计算", status="error", data={"error": msg}))
        return {"error": msg}

    logger.info(f"execute_spec 完成:dataset={state.dataset_id} sheet={spec.sheet} 返回 {len(rows)} 行")
    writer(WSStepInfo(
        step="执行计算",
        status="success",
        data={"row_count": len(rows), "sample": rows[:5]},
    ))
    # sql_result 是 WSAgentState 的字段,下游 chart_agent / interpret_result 直接读
    return {"sql_result": rows, "error": None}
