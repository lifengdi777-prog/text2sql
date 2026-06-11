"""Attribution Agent 的图编排。

    START → parse_target ──(结果不可归因/失败)──→ END   (口径由前端弹层前置给定)
              ↓ 并行 fan-out(二者互不依赖,省一段串行等待)
        ┌─ confirm_phenomenon (现象确认;无数据/持平/不成立 → 置 halt=True)
        └─ plan_dimensions    (LLM 只选维度名,子问题代码模板生成;无维度 → halt)
              ↓ 在 run_dims 汇合(屏障)
          run_dims ──(上游已判终止/全部失败)──→ END
              ↓     (每维度两条单期子查询并发 4 路,纯代码 join 算贡献度)
          synthesize(LLM 只写结论;流末发结构化 attribution_result 事件) → END

注:confirm 与 plan 并行后,任一分支判终止时另一分支可能已发过自己的步骤事件,
   属预期(用户会看到对应说明卡);halt/error 带 or 归并器,同超步双写安全。
"""
from langgraph.graph import END, START, StateGraph

from agent.attribution_agent.nodes.confirm_phenomenon import confirm_phenomenon
from agent.attribution_agent.nodes.parse_target import parse_target
from agent.attribution_agent.nodes.plan_dimensions import plan_dimensions
from agent.attribution_agent.nodes.run_dims import run_dims
from agent.attribution_agent.nodes.synthesize import synthesize
from agent.attribution_agent.schemas import AttributionContext, AttributionState


def _fan_out_after_parse(state: AttributionState):
    # 继续 → 同时进入现象确认与维度规划(并行);否则结束
    if not state.halt:
        return ["confirm_phenomenon", "plan_dimensions"]
    return END


def _route_continue(state: AttributionState) -> str:
    return END if state.halt else "continue"


def _build():
    g = StateGraph(state_schema=AttributionState, context_schema=AttributionContext)
    g.add_node("parse_target", parse_target)
    g.add_node("confirm_phenomenon", confirm_phenomenon)
    g.add_node("plan_dimensions", plan_dimensions)
    g.add_node("run_dims", run_dims)
    g.add_node("synthesize", synthesize)

    g.add_edge(START, "parse_target")
    g.add_conditional_edges("parse_target", _fan_out_after_parse,
                            ["confirm_phenomenon", "plan_dimensions", END])
    # 两条并行分支在 run_dims 汇合(屏障);run_dims 自身先检查 halt
    g.add_edge("confirm_phenomenon", "run_dims")
    g.add_edge("plan_dimensions", "run_dims")
    g.add_conditional_edges("run_dims", _route_continue,
                            {"continue": "synthesize", END: END})
    g.add_edge("synthesize", END)
    return g.compile()


attribution_graph = _build()
