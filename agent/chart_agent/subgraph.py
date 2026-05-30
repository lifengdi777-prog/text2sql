"""Chart Agent 子图。

数据流:
    analyze_data_shape
        ├─ render_error      (state.error / sql_result is None)
        ├─ render_empty      (空结果)
        ├─ render_metric     (单行 numeric)
        ├─ render_table      (行数 > CHART_MAX_ROWS:太多,降级表格)
        └─ build_chart       (LLM 只选 chart_type → 代码确定性构 option → 直接 emit)
                ↓
              END

设计要点:
- 透视长表→宽表、填 series.data 这类确定性变换全部用代码做(option_builder),
  LLM 只从「兼容类型」里挑一个 chart_type(decider)。
- 因为 option 由代码构造,结构天然合法,**不再需要 validate/correct 重试循环**。
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.chart_agent import analyzer
from agent.chart_agent.analyzer import chart_field_map, compatible_chart_types
from agent.chart_agent.decider import decide_chart_type
from agent.chart_agent.option_builder import build_chart_option
from agent.chart_agent.templates import empty as empty_tpl
from agent.chart_agent.templates import error as error_tpl
from agent.chart_agent.templates import metric as metric_tpl
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


# 行数超过此值就不画图(数据量大,可读性差),直接降级 table 展示全量。
CHART_MAX_ROWS = 200


def _make_title(query: Any) -> str:
    """标题:复用用户问题,去掉常见前缀动词,截断到 25 字。"""
    t = str(query or "查询结果").strip()
    for prefix in ("统计", "查询", "查一下", "查", "帮我", "请"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return (t[:25] or "查询结果")


def _build_table_config(rows: list[dict[str, Any]], title: str, reason: str) -> dict[str, Any]:
    """把结果集拍成 table chart_config。前端按 columns + rows 渲染全量数据。"""
    columns = list(rows[0].keys()) if rows else []
    return {
        "chart_type": "table",
        "title": title or "查询结果",
        "columns": columns,
        "rows": [[r.get(c) for c in columns] for r in rows],
        "row_count": len(rows),
        "compatible_types": ["table"],
        "_fallback_reason": reason,
    }


async def analyze_data_shape(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="分析数据形状", status="running"))
    shape = analyzer.analyze(state.sql_result or [])
    writer(WSStepInfo(step="分析数据形状", status="success", data=shape.model_dump()))
    return {"data_shape": shape}


def _route_after_analyze(state: WSAgentState) -> str:
    if state.error or state.sql_result is None:
        return "render_error"
    rows = state.sql_result
    if len(rows) == 0:
        return "render_empty"
    shape = state.data_shape
    if shape and shape.row_count == 1:
        if len(shape.columns) == 1 and shape.columns[0].semantic_type == "numeric":
            return "render_metric"
        if all(c.semantic_type == "numeric" for c in shape.columns):
            return "render_metric"
    # 数据量大:不画图,直接降级 table
    if len(rows) > CHART_MAX_ROWS:
        return "render_table"
    return "build_chart"


# ── 核心:LLM 选型 + 代码构图 ─────────────────────────────────────────
async def build_chart(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """LLM 只决定 chart_type,option 由 option_builder 用代码确定性构造。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    shape = state.data_shape
    query = state.messages[0].content if state.messages else "查询结果"
    title = _make_title(query)

    # 兼容类型 + 字段映射:都由 analyzer 确定性算出(无 LLM)
    compat = compatible_chart_types(shape)
    field_map = chart_field_map(shape)
    non_table = [t for t in compat if t != "table"]

    # 选型:0 个可视化类型→table;唯一类型→直接定;多个→LLM 按用户意图挑
    if not non_table:
        chart_type, reason = "table", "数据形状只适合表格展示"
    elif len(non_table) == 1:
        chart_type, reason = non_table[0], "唯一兼容的可视化类型"
    else:
        chart_type, reason = await decide_chart_type(str(query), shape, compat)

    # 构图
    if chart_type == "table":
        config = _build_table_config(rows, title, reason)
    else:
        config = build_chart_option(chart_type, rows, field_map, title)
        config["compatible_types"] = compat
        config["field_map"] = field_map

    logger.info(f"图表生成(代码构图):type={chart_type}, reason={reason}")
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


# ── 3 个状态卡(deterministic,不调 LLM)─────────────────────────────────
async def render_error(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    config = error_tpl.render(error_message=state.error or "未知错误", original_sql=state.sql)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def render_empty(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    config = empty_tpl.render()
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def render_metric(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    rows = state.sql_result or []
    query = state.messages[0].content if state.messages else ""
    title = str(query)[:50] or "结果"
    config = metric_tpl.render(title=title, rows=rows)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def render_table(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """行数 > CHART_MAX_ROWS:数据太多,直接表格展示全量数据。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    rows = state.sql_result or []
    query = state.messages[0].content if state.messages else "查询结果"
    config = _build_table_config(
        rows, title=str(query)[:50],
        reason=f"结果 {len(rows)} 行 > {CHART_MAX_ROWS},数据量大,降级为表格",
    )
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


def _build():
    sg = StateGraph(state_schema=WSAgentState, context_schema=WSAgentContext)

    sg.add_node("analyze_data_shape", analyze_data_shape)
    sg.add_node("build_chart", build_chart)
    sg.add_node("render_table", render_table)
    sg.add_node("render_error", render_error)
    sg.add_node("render_empty", render_empty)
    sg.add_node("render_metric", render_metric)

    sg.add_edge(START, "analyze_data_shape")
    sg.add_conditional_edges(
        "analyze_data_shape",
        _route_after_analyze,
        {
            "build_chart": "build_chart",
            "render_error": "render_error",
            "render_empty": "render_empty",
            "render_metric": "render_metric",
            "render_table": "render_table",
        },
    )

    sg.add_edge("build_chart", END)
    sg.add_edge("render_table", END)
    sg.add_edge("render_error", END)
    sg.add_edge("render_empty", END)
    sg.add_edge("render_metric", END)

    return sg.compile()


chart_subgraph = _build()
