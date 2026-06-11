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
    """parse_target 的 LLM 结构化输出:归因目标。

    口径前置后,compare_type / baseline_period 不再由 LLM 输出:
    前端弹层给定口径,parse_target 节点按口径从候选基准里代码回填。"""
    # 归因指标(用领域里的说法,如"实际产量")
    metric: str = ""
    # 限定范围(如"华东工厂";没有则空串)
    scope: str = ""
    # 观察期(结果/问题对应的期间,如"2026年3月";按当前日期补全年份)
    target_period: str = ""
    # 现象方向:用户说"下降"=down、"上升/这么高"=up、没说=unknown
    direction: Literal["down", "up", "unknown"] = "unknown"
    # 对比口径:由代码按前端弹层的选择回填(custom 留给二期自定义日期)
    compare_type: Literal["mom", "yoy", "custom"] = "mom"
    # 基准期:代码按口径从下面两个候选里回填
    baseline_period: str = ""
    # 两个候选基准(LLM 必给,供按口径回填/无数据改口径建议用)
    mom_baseline: str = ""
    yoy_baseline: str = ""
    # 连观察期都识别不出(结果无时间信息、问题也没给期间)→ False,
    # 并在 infeasible_reason 说明;有显式口径后单期结果也可归因
    feasible: bool = True
    infeasible_reason: str = ""


class DimensionPlan(BaseModel):
    """plan_dimensions 的 LLM 结构化输出:只选维度名,子问题由代码模板生成。"""
    dimensions: list[str] = []


class AttributionState(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages]
    # 多轮历史(指代消解用),入口注入
    history: list[dict[str, Any]] | None = None
    # ── 结果模式(归因按钮)的种子:用户点按钮那一轮的 问题/SQL/结果行 ──
    # parse_target 据此识别观察期与指标
    seed_question: str | None = None
    seed_sql: str | None = None
    seed_rows: list[dict[str, Any]] | None = None
    # 对比口径:前端弹层选定后随请求传入(口径前置,不再让 LLM 猜/向用户澄清)
    compare_type: Literal["mom", "yoy"] = "mom"
    # 观察期:多期结果(如"各月份产量")由前端弹层让用户选定后传入,代码原样回填
    # (观察期前置,与口径同理 —— 不让 LLM 暗中替用户挑"最近一期");单期结果不传
    target_period: str | None = None
    # parse_target 写入
    target: AttributionTarget | None = None
    # confirm_phenomenon 写入:{target_value, baseline_value, change, change_pct, ...}
    phenomenon: dict[str, Any] | None = None
    # plan_dimensions 写入
    plan: list[dict[str, Any]] | None = None
    # run_dims 写入:[{dimension, members: [{member, target_value, baseline_value,
    #   change, change_pct, contribution_pct}], target_sql, baseline_sql}]
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
