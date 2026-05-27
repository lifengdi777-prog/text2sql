"""Chart Agent 子图。

数据流:
    analyze_data_shape
        ├─ render_error      (state.error / sql_result is None)
        ├─ render_empty      (空结果)
        ├─ render_metric     (单行 numeric)
        ├─ render_table      (行数 > CHART_MAX_ROWS:太多，全量喂不进 LLM，直接表格)
        └─ generate_spec     (LLM 直出 ECharts option，此时 rows 已 ≤ CHART_MAX_ROWS，全量喂)
                ↓
            validate_spec
              ├─ ok → END
              └─ fail → correct_spec → validate_spec(最多重试 MAX_RETRY)
                              ↓ 重试用尽
                          fallback_table → END
"""
from __future__ import annotations

from typing import Any

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


# 行数超过此值就不喂 LLM 画图（采样会漏数据导致图表出错），直接降级 table。
# 设 200 而非 50：长时间序列折线图（如半年日数据）也能保住，200 行喂 LLM 成本可忽略；
# 柱图/饼图的可读性上限另由 validator 把关（pie ≤10、bar ≤30）。
CHART_MAX_ROWS = 200


def _build_table_config(rows: list[dict[str, Any]], title: str, reason: str) -> dict[str, Any]:
    """把结果集拍成 table chart_config。前端按 columns + rows 渲染全量数据。"""
    columns = list(rows[0].keys()) if rows else []
    return {
        "chart_type": "table",
        "title": title or "查询结果",
        "columns": columns,
        "rows": [[r.get(c) for c in columns] for r in rows],
        "row_count": len(rows),
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
    # 数据量大：全量喂不进 LLM（采样会漏数据），直接降级 table
    if len(rows) > CHART_MAX_ROWS:
        return "render_table"
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


# ── 两种 table 出口:大数据直接降级 / LLM 重试用尽兜底 ──────────────────

async def render_table(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """行数 > CHART_MAX_ROWS:数据太多无法全量喂 LLM 画图,直接表格展示全量数据。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    query = state.messages[0].content if state.messages else "查询结果"
    config = _build_table_config(
        rows,
        title=str(query)[:50],
        reason=f"结果 {len(rows)} 行 > {CHART_MAX_ROWS},数据量大,降级为表格",
    )
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def fallback_table(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """LLM 修正 MAX_RETRY 次仍不合法时的最终兜底:把 rows 拍成 table。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    query = state.messages[0].content if state.messages else "查询结果"
    config = _build_table_config(
        rows,
        title=str(query)[:50],
        reason=f"LLM 生成 spec 重试 {MAX_RETRY} 次未通过,降级为 table",
    )
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
    sg.add_node("render_table", render_table)
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
            "render_table": "render_table",
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
    sg.add_edge("render_table", END)
    sg.add_edge("render_error", END)
    sg.add_edge("render_empty", END)
    sg.add_edge("render_metric", END)

    return sg.compile()


chart_subgraph = _build()
