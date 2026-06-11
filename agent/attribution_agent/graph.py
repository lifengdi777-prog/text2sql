"""Attribution Agent 的图编排。

当前(第 1 步骨架):
    START → parse_target ──(口径已明确)──→ plan_dimensions → END
                         └─(需澄清/失败)──→ END

后续接入(第 2/3 步):
    parse_target → confirm_phenomenon(现象确认/基准期无数据提示)
                 → plan_dimensions → run_dims → synthesize → END
"""
from langgraph.graph import END, START, StateGraph

from agent.attribution_agent.nodes.parse_target import parse_target
from agent.attribution_agent.nodes.plan_dimensions import plan_dimensions
from agent.attribution_agent.schemas import AttributionContext, AttributionState


def _route_continue(state: AttributionState) -> str:
    return "continue" if state.should_continue else END


def _build():
    g = StateGraph(state_schema=AttributionState, context_schema=AttributionContext)
    g.add_node("parse_target", parse_target)
    g.add_node("plan_dimensions", plan_dimensions)

    g.add_edge(START, "parse_target")
    g.add_conditional_edges("parse_target", _route_continue,
                            {"continue": "plan_dimensions", END: END})
    g.add_edge("plan_dimensions", END)
    return g.compile()


attribution_graph = _build()
