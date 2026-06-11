"""Attribution Agent 的 State / Context schema。"""
from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable, Literal

from langchain.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, ConfigDict


def _or(a, b):
    """halt / error 的归并器:并行分支(confirm 与 plan)可能在同一超步同时写。
    注意:带归并器的通道由 LangGraph 用类型零值初始化(bool()=False / None),
    所以标志必须设计成"默认零值=继续"的方向(halt=False 继续,任一分支置 True 即终止)。"""
    return a or b


class AttributionTarget(BaseModel):
    """parse_target 的 LLM 结构化输出:归因目标。"""
    # 归因指标(用领域里的说法,如"实际产量")
    metric: str = ""
    # 限定范围(如"华东工厂";没有则空串)
    scope: str = ""
    # 目标期(如"2026年3月";按当前日期补全年份)
    target_period: str = ""
    # 现象方向:用户说"下降"=down、"上升/这么高"=up、没说=unknown
    direction: Literal["down", "up", "unknown"] = "unknown"
    # 对比口径:mom=环比 / yoy=同比 / custom=用户指定基准 / unspecified=没说(要澄清)
    compare_type: Literal["mom", "yoy", "custom", "unspecified"] = "unspecified"
    # 基准期(unspecified 时为空串)
    baseline_period: str = ""
    # 两个候选基准(无论口径是什么都给,供澄清话术/无数据改口径建议用)
    mom_baseline: str = ""
    yoy_baseline: str = ""
    # 结果模式(归因按钮):当前结果是否存在可归因的变化。
    # 单期汇总等没有时间对比的结果 → False,并在 infeasible_reason 说明
    feasible: bool = True
    infeasible_reason: str = ""


class DimensionPlan(BaseModel):
    """plan_dimensions 的 LLM 结构化输出:拆解维度清单。"""
    class Dim(BaseModel):
        name: str       # 维度名(如"工厂")
        question: str   # 自包含子问题,一条覆盖目标期+基准期、按该维度分组

    dimensions: list[Dim] = []


class AttributionState(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages]
    # 多轮历史(指代消解用),入口注入
    history: list[dict[str, Any]] | None = None
    # ── 结果模式(归因按钮)的种子:用户点按钮那一轮的 问题/SQL/结果行 ──
    # parse_target 据此从结果里识别最显著变化,省掉口径澄清(基准就在数据里)
    seed_question: str | None = None
    seed_sql: str | None = None
    seed_rows: list[dict[str, Any]] | None = None
    # parse_target 写入
    target: AttributionTarget | None = None
    # confirm_phenomenon 写入:{target_value, baseline_value, change, change_pct, ...}
    phenomenon: dict[str, Any] | None = None
    # plan_dimensions 写入
    plan: list[dict[str, Any]] | None = None
    # run_dims 写入:[{dimension, question, sql, rows, error}]
    dim_results: list[dict[str, Any]] | None = None
    # synthesize 写入:归因结论(自然语言,带数字论证)
    conclusion: str | None = None
    # 澄清/无数据/现象不成立等提前收尾 → 置 True(路由据此 END)
    halt: Annotated[bool, _or] = False
    error: Annotated[str | None, _or] = None


# run_query 能力签名:自包含问题 → {"rows": list[dict]|None, "sql": str|None, "error": str|None}
RunQuery = Callable[[str], Awaitable[dict]]


class AttributionContext(BaseModel):
    """跨后端复用的关键:只注入「查询能力」与「领域描述」,不含任何 SQL/DuckDB 知识。

    db 入口:    run_query 包装 db_graph.ainvoke;domain_md 用 meta 的表+指标渲染。
    dataset 入口: run_query 包装 dataset_graph.ainvoke;domain_md 用 rendered_schema。
    """
    run_query: Any = None       # RunQuery(pydantic 对 Callable 校验弱,运行时鸭子类型)
    domain_md: str = ""

    model_config = ConfigDict(arbitrary_types_allowed=True)
