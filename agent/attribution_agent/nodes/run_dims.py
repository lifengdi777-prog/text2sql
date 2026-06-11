"""维度执行节点:并发跑各维度的两条单期分组子查询,纯代码 join 算贡献度。

每维度两条子问题(观察期/基准期各一条,单期分组保证行形状是「成员, 值」),
代码按成员 join 后计算 change / change_pct / contribution_pct(成员变化 ÷ 总变化),
消灭 LLM 算数——synthesize 只拿算好的贡献清单照着写结论。

并发 4 路(子查询级别,墙钟 ≈ 最慢一条);每个维度发独立步骤事件,用户全程看得到进展。
单维度失败只跳过该维度(步骤标 error),全部失败才终止。
本节点也是 confirm 与 plan 两条并行分支的汇合屏障:先检查上游是否已判终止。
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from langgraph.runtime import Runtime

from agent.attribution_agent.schemas import AttributionContext, AttributionState
from agent.schemas import WSStepInfo
from core.log import logger

# 每个维度保留的最大成员数(按变化量绝对值排序后截断,长尾对归因没有信息量)
MAX_MEMBERS = 30
# 子查询并发上限(每条都是完整查询管线;每维度 2 条单期查询,比两期对比查询更轻)
MAX_CONCURRENCY = 4


def _to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def _member_values(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    """单期分组结果 → {成员: 数值}。

    列识别启发式:数值列取行内第一个数值;成员列取**取值最多样**的字符串列
    (单期查询里期间/范围若仍出现在结果里,它们是常量列,多样性低,自然被排除)。
    识别不出成员列 → 返回 None,该维度跳过。
    """
    if not rows:
        return None
    distinct: dict[str, set] = {}
    for row in rows:
        for k, v in row.items():
            if isinstance(v, str):
                distinct.setdefault(k, set()).add(v)
    if not distinct:
        return None
    member_key = max(distinct, key=lambda k: len(distinct[k]))

    out: dict[str, float] = {}
    for row in rows:
        member = row.get(member_key)
        value = next((n for n in (_to_number(v) for k, v in row.items()
                                  if k != member_key) if n is not None), None)
        if isinstance(member, str) and value is not None:
            # 同成员多行(意外的细粒度)按求和归并,与"总和"口径一致
            out[member] = out.get(member, 0.0) + value
    return out or None


def _join_members(target: dict[str, float], baseline: dict[str, float],
                  total_change: float) -> list[dict[str, Any]]:
    """两期成员值 join → 贡献清单(缺席期按 0 计,符合求和口径下的新增/消失成员)。"""
    members = []
    for m in {*target, *baseline}:
        tv, bv = target.get(m, 0.0), baseline.get(m, 0.0)
        change = tv - bv
        members.append({
            "member": m, "target_value": tv, "baseline_value": bv,
            "change": change,
            "change_pct": (change / bv * 100) if bv else None,
            "contribution_pct": (change / total_change * 100) if total_change else None,
        })
    members.sort(key=lambda x: abs(x["change"]), reverse=True)
    return members[:MAX_MEMBERS]


async def run_dims(state: AttributionState, runtime: Runtime[AttributionContext]):
    # 汇合屏障:confirm/plan 任一分支已判终止(说明卡已发)→ 直接放行到 END
    if state.halt:
        return {}

    rq = runtime.context.run_query
    writer = runtime.stream_writer
    plan = state.plan or []
    n = len(plan)
    total_change = (state.phenomenon or {}).get("change") or 0.0
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def query(question: str) -> dict[str, Any]:
        async with sem:
            return await rq(question)

    async def one(i: int, dim: dict) -> dict[str, Any] | None:
        step = f"维度拆解:{dim['name']} ({i}/{n})"
        writer(WSStepInfo(step=step, status="running"))
        target_out, base_out = await asyncio.gather(
            query(dim["target_question"]), query(dim["baseline_question"]))
        err = target_out.get("error") or base_out.get("error")
        tm = _member_values(target_out.get("rows") or [])
        bm = _member_values(base_out.get("rows") or [])
        # 任一期查询出错 → 数字不可信,跳过;两期都解析不出成员 → 无数据,跳过。
        # 仅一期为空(无错误)是合法形状:新增/消失的成员,缺席期按 0 计。
        if err or (tm is None and bm is None):
            # 单维度失败:标记该步骤失败但不影响其余维度
            writer(WSStepInfo(step=step, status="error",
                              data={"error": err or "该维度查询无数据,已跳过"}))
            logger.warning(f"归因维度跳过:{dim['name']} -> {err or '无数据'}")
            return None
        members = _join_members(tm or {}, bm or {}, total_change)
        writer(WSStepInfo(step=step, status="success", data={"members": len(members)}))
        return {
            "dimension": dim["name"],
            "members": members,
            "target_sql": target_out.get("sql"),
            "baseline_sql": base_out.get("sql"),
        }

    outs = await asyncio.gather(*(one(i, d) for i, d in enumerate(plan, 1)))
    results = [r for r in outs if r is not None]

    if not results:
        writer(WSStepInfo(step="维度拆解", status="error",
                          data={"error": "所有拆解维度的查询都失败了,无法完成归因"}, finish=True))
        return {"halt": True, "error": "all dims failed"}

    logger.info(f"归因维度执行完成:{[r['dimension'] for r in results]}")
    return {"dim_results": results}
