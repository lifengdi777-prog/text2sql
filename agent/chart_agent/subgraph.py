"""Chart Agent 子图编排。

入口分流逻辑(纯 Python,不消耗 LLM token):
  - state.error / sql_result is None → error 卡
  - sql_result == []                  → empty 卡
  - 单行单列                          → metric 卡
  - 其他                              → analyze → decide → render(走 6 种正常图表)
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.chart_agent import analyzer
from agent.chart_agent.decider import decide_chart
from agent.chart_agent.renderer import render_chart
from agent.chart_agent.schemas import ChartDecision
from agent.chart_agent.templates import empty as empty_tpl
from agent.chart_agent.templates import error as error_tpl
from agent.chart_agent.templates import metric as metric_tpl
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo


# ──────────────────────────────────────────────────────────────────────────
# 入口:分析数据形状(同时也作为分流前的 deterministic 检查点)
# ──────────────────────────────────────────────────────────────────────────

async def analyze_data_shape(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """分析 sql_result 的数据形状。state.error / 空 / 单值 这三种 deterministic 情况
    在 _route_after_analyze 里分流,这里只做 analyze。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="分析数据形状", status="running"))

    shape = analyzer.analyze(state.sql_result or [])

    writer(WSStepInfo(
        step="分析数据形状",
        status="success",
        data=shape.model_dump(),
    ))
    return {"data_shape": shape}


# ──────────────────────────────────────────────────────────────────────────
# 分流:决定走 4 条分支中的哪一条
# ──────────────────────────────────────────────────────────────────────────

def _route_after_analyze(state: WSAgentState) -> str:
    """路由函数:看 state 决定下一步去哪个 render 节点。"""
    # 1. SQL 报错(execute_sql 出异常,sql_result=None 且 error 有值)
    if state.error or state.sql_result is None:
        return "render_error"

    rows = state.sql_result
    # 2. 空结果集
    if len(rows) == 0:
        return "render_empty"

    # 3. 单行 + 单列 numeric → 指标卡
    shape = state.data_shape
    if shape and shape.row_count == 1 and len(shape.columns) == 1:
        if shape.columns[0].semantic_type == "numeric":
            return "render_metric"
        # 单行单列但不是数字,降级走 table
    if shape and shape.row_count == 1 and len(shape.columns) <= 2:
        # 像 SELECT SUM(x) AS y, AVG(z) 这种单行多列也按指标卡
        if all(c.semantic_type == "numeric" for c in shape.columns):
            return "render_metric"

    # 4. 走 LLM 决策正常图表
    return "decide_chart"


# ──────────────────────────────────────────────────────────────────────────
# 3 个状态卡 render 节点(deterministic,不调 LLM)
# ──────────────────────────────────────────────────────────────────────────

async def render_error(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    decision = ChartDecision(
        chart_type="error",
        title="查询失败",
        reason="SQL 执行报错,渲染 error 卡",
    )
    config = error_tpl.render(
        decision,
        state.sql_result or [],
        error_message=state.error or "未知错误",
        original_sql=state.sql,
    )
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_decision": decision, "chart_config": config}


async def render_empty(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    decision = ChartDecision(
        chart_type="empty",
        title="查询无数据",
        reason="结果集为空,渲染 empty 卡",
    )
    config = empty_tpl.render(decision, [])
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_decision": decision, "chart_config": config}


async def render_metric(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    shape = state.data_shape
    # 标题:用 query 或第一列的列名兜底
    query = state.messages[0].content if state.messages else ""
    first_col = shape.columns[0].name if shape and shape.columns else "value"

    decision = ChartDecision(
        chart_type="metric",
        title=str(query)[:50] or first_col,
        y_field=first_col,
        reason="单行数值结果,渲染指标卡",
    )
    config = metric_tpl.render(decision, rows)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_decision": decision, "chart_config": config}


# ──────────────────────────────────────────────────────────────────────────
# 编译子图
# ──────────────────────────────────────────────────────────────────────────

def _build():
    sg = StateGraph(state_schema=WSAgentState, context_schema=WSAgentContext)

    sg.add_node("analyze_data_shape", analyze_data_shape)
    sg.add_node("decide_chart", decide_chart)
    sg.add_node("render_chart", render_chart)
    sg.add_node("render_error", render_error)
    sg.add_node("render_empty", render_empty)
    sg.add_node("render_metric", render_metric)

    sg.add_edge(START, "analyze_data_shape")
    # 4 条分支:LLM 决策 / error / empty / metric
    sg.add_conditional_edges(
        "analyze_data_shape",
        _route_after_analyze,
        {
            "decide_chart": "decide_chart",
            "render_error": "render_error",
            "render_empty": "render_empty",
            "render_metric": "render_metric",
        },
    )
    # 正常分支:decide → render
    sg.add_edge("decide_chart", "render_chart")
    # 4 个终点都到 END
    sg.add_edge("render_chart", END)
    sg.add_edge("render_error", END)
    sg.add_edge("render_empty", END)
    sg.add_edge("render_metric", END)

    return sg.compile()


chart_subgraph = _build()
