"""维度执行节点:并发跑各维度的"两期对比"子查询,收集小表供综合归因。

并发 3 路(墙钟 ≈ 最慢一条,而非各条之和);每个维度发独立步骤事件,
用户全程看得到进展。单维度失败只跳过该维度(步骤标 error),全部失败才终止。
本节点也是 confirm 与 plan 两条并行分支的汇合屏障:先检查上游是否已判终止。
"""
from __future__ import annotations

import asyncio
from typing import Any

from langgraph.runtime import Runtime

from agent.attribution_agent.schemas import AttributionContext, AttributionState
from agent.schemas import WSStepInfo
from core.log import logger

# 每个维度结果保留的最大行数(维度对比都是小表,够 synthesize 用即可)
MAX_DIM_ROWS = 60
# 维度子查询并发上限(每条都是完整查询管线,3 路在 LLM/连接池压力与速度间取平衡)
MAX_CONCURRENCY = 3


async def run_dims(state: AttributionState, runtime: Runtime[AttributionContext]):
    # 汇合屏障:confirm/plan 任一分支已判终止(说明卡已发)→ 直接放行到 END
    if state.halt:
        return {}

    rq = runtime.context.run_query
    writer = runtime.stream_writer
    plan = state.plan or []
    n = len(plan)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(i: int, dim: dict) -> dict[str, Any] | None:
        step = f"维度拆解:{dim['name']} ({i}/{n})"
        async with sem:
            writer(WSStepInfo(step=step, status="running"))
            out = await rq(dim["question"])
        rows = out.get("rows")
        if out.get("error") or not rows:
            # 单维度失败:标记该步骤失败但不影响其余维度
            writer(WSStepInfo(step=step, status="error",
                              data={"error": out.get("error") or "该维度查询无数据,已跳过"}))
            logger.warning(f"归因维度跳过:{dim['name']} -> {out.get('error') or '无数据'}")
            return None
        writer(WSStepInfo(step=step, status="success", data={"rows": len(rows)}))
        return {
            "dimension": dim["name"],
            "question": dim["question"],
            "sql": out.get("sql"),
            "rows": rows[:MAX_DIM_ROWS],
        }

    outs = await asyncio.gather(*(one(i, d) for i, d in enumerate(plan, 1)))
    results = [r for r in outs if r is not None]

    if not results:
        writer(WSStepInfo(step="维度拆解", status="error",
                          data={"error": "所有拆解维度的查询都失败了,无法完成归因"}, finish=True))
        return {"halt": True, "error": "all dims failed"}

    logger.info(f"归因维度执行完成:{[r['dimension'] for r in results]}")
    return {"dim_results": results}
