"""Attribution Agent 的图编排。

    START → parse_target ──(需澄清口径/失败)──→ END
              ↓
          confirm_phenomenon ──(基准期/目标期无数据、现象不成立)──→ END
              ↓
          plan_dimensions ──(无可用维度/失败)──→ END
              ↓
          run_dims(逐维度子查询) ──(全部失败)──→ END
              ↓
          synthesize(综合归因:结论 + 主维度表 + 对比图) → END
"""
from langgraph.graph import END, START, StateGraph

from agent.attribution_agent.nodes.confirm_phenomenon import confirm_phenomenon
from agent.attribution_agent.nodes.parse_target import parse_target
from agent.attribution_agent.nodes.plan_dimensions import plan_dimensions
from agent.attribution_agent.nodes.run_dims import run_dims
from agent.attribution_agent.nodes.synthesize import synthesize
from agent.attribution_agent.schemas import AttributionContext, AttributionState


def _route_continue(state: AttributionState) -> str:
    return "continue" if state.should_continue else END


def _build():
    g = StateGraph(state_schema=AttributionState, context_schema=AttributionContext)
    g.add_node("parse_target", parse_target)
    g.add_node("confirm_phenomenon", confirm_phenomenon)
    g.add_node("plan_dimensions", plan_dimensions)
    g.add_node("run_dims", run_dims)
    g.add_node("synthesize", synthesize)

    g.add_edge(START, "parse_target")
    g.add_conditional_edges("parse_target", _route_continue,
                            {"continue": "confirm_phenomenon", END: END})
    g.add_conditional_edges("confirm_phenomenon", _route_continue,
                            {"continue": "plan_dimensions", END: END})
    g.add_conditional_edges("plan_dimensions", _route_continue,
                            {"continue": "run_dims", END: END})
    g.add_conditional_edges("run_dims", _route_continue,
                            {"continue": "synthesize", END: END})
    g.add_edge("synthesize", END)
    return g.compile()


attribution_graph = _build()
