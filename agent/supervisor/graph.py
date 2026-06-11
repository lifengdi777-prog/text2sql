"""Supervisor 父图:route_intent 分流,包装节点转交子 agent 执行。

结构:
    START → route_intent ──(chart)──→ run_chart_agent → END
                         └─(query)──→ run_query_agent → END

不动子 agent 内部:包装节点里 ainvoke 子图,子图节点 stream_writer 写出的
WSStepInfo 事件经 contextvars 自动冒泡——入口 astream 已带 subgraphs=True,
与此前 chart 子图嵌入主图时的事件冒泡是同一机制,前端协议零变化。
(归因分析不在此路由:它是结果卡上的按钮,直达 POST /agent/attribution。)
"""
from __future__ import annotations

from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.chart_agent import chart_subgraph
from agent.chart_agent.schemas import ChartAgentContext, ChartAgentState
from agent.dataset_agent.graph import dataset_graph
from agent.dataset_agent.schemas import DatasetAgentState
from agent.db_agent.graph import graph as db_graph
from agent.schemas import WSAgentState
from agent.supervisor.route_intent import route_intent
from agent.supervisor.schemas import SupervisorContext, SupervisorState


def build_supervisor(query_graph, make_query_state: Callable[[SupervisorState], object]):
    """构建一个 supervisor 实例。

    query_graph:      查询子 agent 的编译图(db_agent / dataset_agent)
    make_query_state: 从 SupervisorState 构造该子 agent 的初始 State
    """

    async def run_query_agent(state: SupervisorState, runtime: Runtime[SupervisorContext]):
        # 子图事件直接冒泡给前端,supervisor 自身不需要回写任何状态
        await query_graph.ainvoke(make_query_state(state), context=runtime.context.query_context)
        return {}

    async def run_chart_agent(state: SupervisorState, runtime: Runtime[SupervisorContext]):
        # messages 原样转交(绘图请求本身就是选型上下文,如"画成柱状图");
        # 数据由 chart_agent 的 load_rows 节点按 conversation_id 从会话历史自取。
        child = ChartAgentState(messages=state.messages)
        ctx = ChartAgentContext(conversation_id=runtime.context.conversation_id)
        await chart_subgraph.ainvoke(child, context=ctx)
        return {}

    def _dispatch(state: SupervisorState) -> str:
        return "run_chart_agent" if state.route == "chart" else "run_query_agent"

    g = StateGraph(state_schema=SupervisorState, context_schema=SupervisorContext)
    g.add_node("route_intent", route_intent)
    g.add_node("run_query_agent", run_query_agent)
    g.add_node("run_chart_agent", run_chart_agent)

    g.add_edge(START, "route_intent")
    g.add_conditional_edges(
        "route_intent",
        _dispatch,
        {"run_chart_agent": "run_chart_agent", "run_query_agent": "run_query_agent"},
    )
    g.add_edge("run_query_agent", END)
    g.add_edge("run_chart_agent", END)
    return g.compile()


# 问数页入口:db_agent | chart_agent
db_supervisor = build_supervisor(
    db_graph,
    lambda s: WSAgentState(messages=s.messages, history=s.history,
                           intent_pre_parsed=s.intent_pre_parsed),
)

# 数据集页入口:dataset_agent | chart_agent
dataset_supervisor = build_supervisor(
    dataset_graph,
    lambda s: DatasetAgentState(messages=s.messages, dataset_id=s.dataset_id, history=s.history,
                                intent_pre_parsed=s.intent_pre_parsed),
)
