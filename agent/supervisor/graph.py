"""Supervisor 父图:route_intent 分流,包装节点转交子 agent 执行。

结构:
    START → route_intent ──(chart)──→ run_chart_agent → END
                         └─(query)──→ run_query_agent ─┬─(纯查询)────────────→ END
                                                       └─(带画图诉求且有结果)→ run_chart_agent → END

「查询+画图」组合("用饼图展示各工厂产量"):route_intent 记下 chart_directive,
run_query_agent 把查询末态的结果行接进 SupervisorState,run_chart_agent 直传给
chart_agent 出图(load_rows 见 sql_result 已就位直接放行,不回读会话历史——
也避免了本轮结果尚未落库的时序竞态)。一轮 SSE 里先后出现「结果行 finish 事件 +
图表 finish 事件」,前端 mergeReplyMessage 与落库 ReplyAccumulator 本就支持该协议。

不动子 agent 内部:包装节点里 ainvoke 子图,子图节点 stream_writer 写出的
WSStepInfo 事件经 contextvars 自动冒泡——入口 astream 已带 subgraphs=True,
与此前 chart 子图嵌入主图时的事件冒泡是同一机制,前端协议零变化。
"""
from __future__ import annotations

from typing import Callable

from langchain.messages import HumanMessage
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
        # 子图事件直接冒泡给前端;末态只在「带画图诉求」时接住,纯查询零开销
        final = await query_graph.ainvoke(make_query_state(state),
                                          context=runtime.context.query_context) or {}
        rows = final.get("sql_result")
        if state.chart_directive and rows and not final.get("error"):
            # 查询成功且有结果 → 桥接给 run_chart_agent 直传出图。
            # 查询失败/守门拦截/空结果 → 不接力(子 agent 已给出错误说明或引导,再叠图卡是噪音)。
            # sql:db_agent 写 state.sql(LIMIT 截断后),dataset_agent 写 generated_sql。
            return {"query_result": rows,
                    "query_sql": final.get("sql") or final.get("generated_sql")}
        return {}

    async def run_chart_agent(state: SupervisorState, runtime: Runtime[SupervisorContext]):
        if state.query_result is not None:
            # 「查询+画图」组合轮:本轮刚查出的数据直传(load_rows 见 sql_result 非 None 放行)。
            # messages 用画图指令(chart_agent 据此识别点名图型,如"饼"→pie);
            # source_question 用改写后的查询问题(图表标题/前端报告都要它,
            # 不能用原始消息——其中混着"用饼图展示"这类指令词)。
            child = ChartAgentState(
                messages=[HumanMessage(content=f"用{state.chart_directive}展示")],
                sql_result=state.query_result,
                sql=state.query_sql,
                source_question=str(state.messages[-1].content),
            )
        else:
            # 纯画图轮:messages 原样转交(绘图请求本身就是选型上下文,如"画成柱状图");
            # 数据由 chart_agent 的 load_rows 节点按 conversation_id 从会话历史自取。
            child = ChartAgentState(messages=state.messages)
        ctx = ChartAgentContext(conversation_id=runtime.context.conversation_id)
        await chart_subgraph.ainvoke(child, context=ctx)
        return {}

    def _dispatch(state: SupervisorState) -> str:
        return "run_chart_agent" if state.route == "chart" else "run_query_agent"

    def _after_query(state: SupervisorState) -> str:
        # 带画图诉求且查询桥接了结果 → 接力出图;否则本轮到此为止
        return "run_chart_agent" if (state.chart_directive and state.query_result) else END

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
    g.add_conditional_edges(
        "run_query_agent",
        _after_query,
        {"run_chart_agent": "run_chart_agent", END: END},
    )
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
