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


# LLM 给的字段映射列名要校验:无效/缺失的字段逐个用规则映射兜底,
# 防止 LLM 编造列名导致 option_builder 取不到数据、画出空图。
def _resolve_field_map(decision, shape) -> dict:
    valid = {c.name for c in shape.columns} if shape else set()
    rule_fm = chart_field_map(shape)  # 规则兜底映射

    fm: dict = {}
    dim = decision.x_field if decision.x_field in valid else rule_fm.get("dimension")
    if dim:
        fm["dimension"] = dim
    measure = decision.value_field if decision.value_field in valid else rule_fm.get("measure")
    if measure:
        fm["measure"] = measure
    series = decision.series_field if decision.series_field in valid else rule_fm.get("series")
    if series:
        fm["series"] = series
    # 多指标分组柱:优先 LLM 给的(过滤掉无效列名),否则用规则
    if decision.value_fields:
        vf = [f for f in decision.value_fields if f in valid]
        if vf:
            fm["measures"] = vf
    elif rule_fm.get("measures"):
        fm["measures"] = rule_fm["measures"]
    return fm


# ── 核心:LLM 选型 + 给字段映射,option 由代码按映射构造 ─────────────────
async def build_chart(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """LLM 决定 chart_type + 字段映射;option_builder 按映射用代码透视、填数据。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    shape = state.data_shape
    query = state.messages[0].content if state.messages else "查询结果"
    title = _make_title(query)

    compat = compatible_chart_types(shape)  # 兼容类型集:确定性算出(无 LLM)
    non_table = [t for t in compat if t != "table"]

    if not non_table:
        # 没有可视化类型 → 表格,无需 LLM、无需字段映射
        chart_type, reason = "table", "数据形状只适合表格展示"
        config = _build_table_config(rows, title, reason)
    else:
        # 有可画的图 → LLM 选型 + 给字段映射
        decision = await decide_chart_type(str(query), shape, compat)
        if decision is None:
            # LLM 失败兜底:兼容集首项 + 规则映射
            chart_type = non_table[0]
            field_map = chart_field_map(shape)
            reason = "选型调用失败,回退兼容集首项 + 规则映射"
        else:
            # chart_type 越界则回退;字段映射逐项校验 + 规则兜底
            chart_type = decision.chart_type if decision.chart_type in compat else non_table[0]
            field_map = _resolve_field_map(decision, shape)
            reason = decision.reason

        if chart_type == "table":
            config = _build_table_config(rows, title, reason)
        else:
            config = build_chart_option(chart_type, rows, field_map, title)
            config["compatible_types"] = compat
            config["field_map"] = field_map

    logger.info(f"图表生成(LLM 选型+映射, 代码填数据):type={chart_type}, reason={reason}")
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
