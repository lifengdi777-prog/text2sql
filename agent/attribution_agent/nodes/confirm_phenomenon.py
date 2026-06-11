"""现象确认节点:查目标期与基准期的指标总量,确认"下降/上升"是否成立并量化。

零 LLM,纯代码 + 两次子查询(并发):
  - 基准期没数据 → 说明卡 + 改用另一口径的结构化建议(suggest_compare_type,
    面板据此渲染「改用环比/同比」按钮,点了带新口径重发请求);
  - 目标期没数据 → 说明卡;
  - 两期持平(总变化为 0)→ 没有可归因的变化,说明后结束(也避免贡献度除零);
  - 用户说"下降"但实际没降(或说"上升"但没升)→ 用数字说明实情,无需归因,结束;
  - 现象成立 → 量化(差值/降幅)写入 state.phenomenon,继续维度拆解。

为保证取数确定性,两期分别用「单值聚合」子问题查询(结果就一个数,不需要解析行形状)。
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from langgraph.runtime import Runtime

from agent.attribution_agent.schemas import AttributionContext, AttributionState, AttributionTarget
from agent.schemas import WSStepInfo
from core.log import logger

_STEP = "确认现象"


def _total_question(t: AttributionTarget, period: str) -> str:
    return f"{period}{t.scope or ''}的{t.metric}总和是多少"


def _first_number(rows: list[dict[str, Any]] | None) -> float | None:
    """从单值聚合结果里取第一个数值(bool 不算数)。取不到 → 视为该期无数据。"""
    if not rows:
        return None
    for v in rows[0].values():
        if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
            return float(v)
    return None


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def _other_basis(t: AttributionTarget) -> tuple[str, str] | None:
    """当前口径走不通时的另一口径建议:(compare_type, 基准期);候选基准缺失则不建议。"""
    if t.compare_type == "yoy" and t.mom_baseline:
        return "mom", t.mom_baseline
    if t.compare_type == "mom" and t.yoy_baseline:
        return "yoy", t.yoy_baseline
    return None


async def confirm_phenomenon(state: AttributionState, runtime: Runtime[AttributionContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step=_STEP, status="running"))
    t = state.target
    assert t is not None
    rq = runtime.context.run_query
    if rq is None:
        writer(WSStepInfo(step=_STEP, status="error",
                          data={"error": "归因服务未正确初始化(缺少查询能力)"}, finish=True))
        return {"halt": True, "error": "run_query missing"}

    target_out, base_out = await asyncio.gather(
        rq(_total_question(t, t.target_period)),
        rq(_total_question(t, t.baseline_period)),
    )
    tv = _first_number(target_out.get("rows")) if not target_out.get("error") else None
    bv = _first_number(base_out.get("rows")) if not base_out.get("error") else None

    basis_cn = {"mom": "环比", "yoy": "同比", "custom": "对比"}.get(t.compare_type, "对比")

    # 基准期无数据 → 提示 + 结构化改口径建议(没有数据要提示,不硬算)
    if bv is None:
        other = _other_basis(t)
        data: dict[str, Any] = {"clarify": f"缺少{t.baseline_period}的数据,无法{basis_cn}对比"}
        if other:
            other_cn = "环比" if other[0] == "mom" else "同比"
            data["clarify"] += f",可改用{other_cn}(对比{other[1]})"
            data["suggest_compare_type"] = other[0]
        writer(WSStepInfo(step=_STEP, status="success", data=data, finish=True))
        logger.info(f"归因终止:基准期无数据({t.baseline_period})")
        return {"halt": True}

    # 目标期无数据 → 提示
    if tv is None:
        writer(WSStepInfo(
            step=_STEP, status="success",
            data={"clarify": f"没有找到{t.target_period}{t.scope or ''}的{t.metric}数据,无法归因"},
            finish=True,
        ))
        logger.info(f"归因终止:目标期无数据({t.target_period})")
        return {"halt": True}

    change = tv - bv
    pct = (change / bv * 100) if bv else None
    desc = (f"{t.target_period}{t.scope or ''}的{t.metric}为 {_fmt(tv)},"
            f"{t.baseline_period}为 {_fmt(bv)},"
            f"变化 {_fmt(change)}" + (f"({pct:+.1f}%)" if pct is not None else ""))

    # 两期持平 → 没有可归因的变化(也避免下游贡献度除零)
    if change == 0:
        writer(WSStepInfo(
            step=_STEP, status="success",
            data={"clarify": f"{desc} —— 两期持平,没有可归因的变化"}, finish=True,
        ))
        logger.info(f"归因终止:两期持平({desc})")
        return {"halt": True}

    # 现象核实:用户说"下降"但实际没降(或反之)→ 说明实情,无需归因
    if (t.direction == "down" and change >= 0) or (t.direction == "up" and change <= 0):
        actual = "并未下降,反而上升" if t.direction == "down" and change > 0 else \
                 "并未上升,反而下降" if t.direction == "up" and change < 0 else "基本持平"
        writer(WSStepInfo(
            step=_STEP, status="success",
            data={"clarify": f"{desc} —— {actual},无需做{ '下降' if t.direction == 'down' else '上升' }归因"},
            finish=True,
        ))
        logger.info(f"归因终止:现象不成立({desc})")
        return {"halt": True}

    phenomenon = {
        "target_value": tv, "baseline_value": bv,
        "change": change, "change_pct": pct,
        # 面板结构化展示用的口径信息(attribution_result.phenomenon 原样带出)
        "target_period": t.target_period, "baseline_period": t.baseline_period,
        "metric": t.metric, "scope": t.scope, "compare_type": t.compare_type,
        "description": desc,
        "target_sql": target_out.get("sql"), "baseline_sql": base_out.get("sql"),
    }
    writer(WSStepInfo(step=_STEP, status="success", data={"description": desc}))
    logger.info(f"归因现象确认:{desc}")
    return {"phenomenon": phenomenon}
