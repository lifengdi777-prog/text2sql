"""Chart Agent 子图。

数据流:
    analyze_data_shape
        ├─ render_error      (state.error / sql_result is None)
        ├─ render_empty      (空结果)
        ├─ render_metric     (单行 numeric)
        └─ generate_spec     (LLM 直出 ECharts option)
                ↓
            validate_spec
              ├─ ok → END
              └─ fail → correct_spec → validate_spec(最多重试 MAX_RETRY)
                              ↓ 重试用尽
                          fallback_table → END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.chart_agent import analyzer
from agent.chart_agent.corrector import MAX_RETRY, correct_spec
from agent.chart_agent.generator import generate_spec
from agent.chart_agent.templates import empty as empty_tpl
from agent.chart_agent.templates import error as error_tpl
from agent.chart_agent.templates import metric as metric_tpl
from agent.chart_agent.validator import validate_spec
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo


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
    return "generate_spec"


def _route_after_validate(state: WSAgentState) -> str:
    """validate 后路由:无 issues → END;有 issues → correct(达上限则 fallback)。"""
    if not state.chart_issues:
        return END
    if state.chart_retry_count >= MAX_RETRY:
        return "fallback_table"
    return "correct_spec"


# ── 3 个状态卡(deterministic,不调 LLM)─────────────────────────────────

async def render_error(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    config = error_tpl.render(
        error_message=state.error or "未知错误",
        original_sql=state.sql,
    )
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
    title = str(query)[:50] or (rows[0].keys() and list(rows[0].keys())[0]) or "结果"
    config = metric_tpl.render(title=title, rows=rows)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


# ── 兜底:重试用尽时把数据按 table 渲染 ─────────────────────────────────

async def fallback_table(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """LLM 修正 MAX_RETRY 次仍不合法时的最终兜底:无脑把 rows 拍成 table。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    columns = list(rows[0].keys()) if rows else []
    query = state.messages[0].content if state.messages else "查询结果"
    config = {
        "chart_type": "table",
        "title": str(query)[:50] or "查询结果",
        "columns": columns,
        "rows": [[r.get(c) for c in columns] for r in rows],
        "row_count": len(rows),
        "_fallback_reason": f"LLM 生成 spec 重试 {MAX_RETRY} 次未通过,降级为 table",
    }
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


# ── validate 通过后,把 chart_config 推流给前端 ─────────────────────────

async def emit_chart_config(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """validate 通过后:把 validator 写入的 chart_config 作为 finish 事件推给前端。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(
        step="生成图表",
        status="success",
        data=state.chart_config,
        finish=True,
    ))
    return {}


def _build():
    sg = StateGraph(state_schema=WSAgentState, context_schema=WSAgentContext)

    sg.add_node("analyze_data_shape", analyze_data_shape)
    sg.add_node("generate_spec", generate_spec)
    sg.add_node("validate_spec", validate_spec)
    sg.add_node("correct_spec", correct_spec)
    sg.add_node("fallback_table", fallback_table)
    sg.add_node("emit_chart_config", emit_chart_config)
    sg.add_node("render_error", render_error)
    sg.add_node("render_empty", render_empty)
    sg.add_node("render_metric", render_metric)

    sg.add_edge(START, "analyze_data_shape")
    sg.add_conditional_edges(
        "analyze_data_shape",
        _route_after_analyze,
        {
            "generate_spec": "generate_spec",
            "render_error": "render_error",
            "render_empty": "render_empty",
            "render_metric": "render_metric",
        },
    )

    # 正常分支:generate → validate ⇄ correct → emit / fallback
    sg.add_edge("generate_spec", "validate_spec")
    sg.add_conditional_edges(
        "validate_spec",
        _route_after_validate,
        {
            END: "emit_chart_config",
            "correct_spec": "correct_spec",
            "fallback_table": "fallback_table",
        },
    )
    sg.add_edge("correct_spec", "validate_spec")

    # 所有终点
    sg.add_edge("emit_chart_config", END)
    sg.add_edge("fallback_table", END)
    sg.add_edge("render_error", END)
    sg.add_edge("render_empty", END)
    sg.add_edge("render_metric", END)

    return sg.compile()


chart_subgraph = _build()
