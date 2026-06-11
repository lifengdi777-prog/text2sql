"""维度执行节点:逐维度跑"两期对比"子查询,收集小表供综合归因。

每个维度发独立步骤事件("维度拆解:工厂 (1/3)"),用户全程看得到进展。
单维度失败只跳过该维度(步骤标 error),不影响其余;全部失败才终止。
"""
from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from agent.attribution_agent.schemas import AttributionContext, AttributionState
from agent.schemas import WSStepInfo
from core.log import logger

# 每个维度结果保留的最大行数(维度对比都是小表,够 synthesize 用即可)
MAX_DIM_ROWS = 60


async def run_dims(state: AttributionState, runtime: Runtime[AttributionContext]):
    rq = runtime.context.run_query
    writer = runtime.stream_writer
    plan = state.plan or []
    results: list[dict[str, Any]] = []
    n = len(plan)

    for i, dim in enumerate(plan, 1):
        step = f"维度拆解:{dim['name']} ({i}/{n})"
        writer(WSStepInfo(step=step, status="running"))
        out = await rq(dim["question"])
        rows = out.get("rows")
        if out.get("error") or not rows:
            # 单维度失败:标记该步骤失败但流程继续(其余维度仍可支撑归因)
            writer(WSStepInfo(step=step, status="error",
                              data={"error": out.get("error") or "该维度查询无数据,已跳过"}))
            logger.warning(f"归因维度跳过:{dim['name']} -> {out.get('error') or '无数据'}")
            continue
        results.append({
            "dimension": dim["name"],
            "question": dim["question"],
            "sql": out.get("sql"),
            "rows": rows[:MAX_DIM_ROWS],
        })
        writer(WSStepInfo(step=step, status="success", data={"rows": len(rows)}))

    if not results:
        writer(WSStepInfo(step="维度拆解", status="error",
                          data={"error": "所有拆解维度的查询都失败了,无法完成归因"}, finish=True))
        return {"should_continue": False, "error": "all dims failed"}

    logger.info(f"归因维度执行完成:{[r['dimension'] for r in results]}")
    return {"dim_results": results}
