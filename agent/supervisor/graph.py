"""Supervisor 父图:route_intent 分流,包装节点转交子 agent 执行。

结构:
    START → route_intent ──(chart)──────→ run_chart_agent → END
                         ├─(attribution)→ run_attribution_agent → END
                         └─(query/其他)─→ run_query_agent → END

不动子 agent 内部:包装节点里 ainvoke 子图,子图节点 stream_writer 写出的
WSStepInfo 事件经 contextvars 自动冒泡——入口 astream 已带 subgraphs=True,
与此前 chart 子图嵌入主图时的事件冒泡是同一机制,前端协议零变化。
(归因的子查询不在此列:适配器内部用 astream 自消费静默执行,事件不外漏。)
"""
from __future__ import annotations

from typing import Awaitable, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.attribution_agent import attribution_graph
from agent.attribution_agent.adapters import make_dataset_run_query, make_db_run_query
from agent.attribution_agent.schemas import AttributionContext, AttributionState
from agent.chart_agent import chart_subgraph
from agent.chart_agent.schemas import ChartAgentContext, ChartAgentState
from agent.dataset_agent.graph import dataset_graph
from agent.dataset_agent.schemas import DatasetAgentState
from agent.db_agent.graph import graph as db_graph
from agent.schemas import WSAgentState
from agent.supervisor.route_intent import route_intent
from agent.supervisor.schemas import SupervisorContext, SupervisorState

# 归因输入构造器签名:(SupervisorState, SupervisorContext) → (run_query, domain_md)
MakeAttributionInputs = Callable[[SupervisorState, SupervisorContext], Awaitable[tuple]]


async def _db_attribution_inputs(state: SupervisorState, ctx: SupervisorContext) -> tuple:
    """db 入口:run_query 包装 db_agent;领域描述用 meta 的表+指标渲染(复用意图节点的渲染)。"""
    from agent.db_agent.nodes.parse_query_intention import _render_domain

    qc = ctx.query_context  # WSAgentContext(数据源/库已作用域化)
    async with qc.meta_repo() as repo:
        tables = await repo.get_all_tables()
        metrics = await repo.get_all_metrics()
    return make_db_run_query(qc), _render_domain(tables, metrics)


async def _dataset_attribution_inputs(state: SupervisorState, ctx: SupervisorContext) -> tuple:
    """dataset 入口:run_query 包装 dataset_agent;领域描述用数据集 schema 渲染。"""
    from services.dataset_loader import get_dataset_info, render_schema_for_prompt

    user_id = getattr(ctx.query_context, "user_id", "anonymous")
    dataset_id = state.dataset_id
    info = await get_dataset_info(dataset_id) if dataset_id is not None else None
    domain_md = render_schema_for_prompt(info["schema"] or {}) if info else ""
    return make_dataset_run_query(user_id, dataset_id), domain_md


def build_supervisor(query_graph, make_query_state: Callable[[SupervisorState], object],
                     make_attribution_inputs: MakeAttributionInputs):
    """构建一个 supervisor 实例。

    query_graph:             查询子 agent 的编译图(db_agent / dataset_agent)
    make_query_state:        从 SupervisorState 构造该子 agent 的初始 State
    make_attribution_inputs: 为归因 agent 构造 (run_query, domain_md)(按入口闭包注入)
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

    async def run_attribution_agent(state: SupervisorState, runtime: Runtime[SupervisorContext]):
        # 归因 agent 不懂 SQL/DuckDB:这里按入口注入查询能力与领域描述
        run_query, domain_md = await make_attribution_inputs(state, runtime.context)
        child = AttributionState(messages=state.messages, history=state.history)
        ctx = AttributionContext(run_query=run_query, domain_md=domain_md)
        await attribution_graph.ainvoke(child, context=ctx)
        return {}

    def _dispatch(state: SupervisorState) -> str:
        if state.route == "chart":
            return "run_chart_agent"
        if state.route == "attribution":
            return "run_attribution_agent"
        return "run_query_agent"

    g = StateGraph(state_schema=SupervisorState, context_schema=SupervisorContext)
    g.add_node("route_intent", route_intent)
    g.add_node("run_query_agent", run_query_agent)
    g.add_node("run_chart_agent", run_chart_agent)
    g.add_node("run_attribution_agent", run_attribution_agent)

    g.add_edge(START, "route_intent")
    g.add_conditional_edges(
        "route_intent",
        _dispatch,
        {"run_chart_agent": "run_chart_agent",
         "run_attribution_agent": "run_attribution_agent",
         "run_query_agent": "run_query_agent"},
    )
    g.add_edge("run_query_agent", END)
    g.add_edge("run_chart_agent", END)
    g.add_edge("run_attribution_agent", END)
    return g.compile()


# 问数页入口:db_agent | chart_agent | attribution_agent
db_supervisor = build_supervisor(
    db_graph,
    lambda s: WSAgentState(messages=s.messages, history=s.history,
                           intent_pre_parsed=s.intent_pre_parsed),
    _db_attribution_inputs,
)

# 数据集页入口:dataset_agent | chart_agent | attribution_agent
dataset_supervisor = build_supervisor(
    dataset_graph,
    lambda s: DatasetAgentState(messages=s.messages, dataset_id=s.dataset_id, history=s.history,
                                intent_pre_parsed=s.intent_pre_parsed),
    _dataset_attribution_inputs,
)
