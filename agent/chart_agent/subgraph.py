"""Chart Agent 子图。

数据流:
    load_rows            (rows 已回传 → 放行;否则从会话历史取数;取不到 → 说明卡结束)
        ↓
    analyze_data_shape
        ├─ render_error      (state.error / sql_result is None)
        ├─ render_empty      (空结果)
        ├─ render_metric     (单行 numeric)
        ├─ render_table      (行数 > CHART_MAX_ROWS:太多,降级表格)
        └─ build_chart       (LLM 判列+选型+映射 → enforce_limits 校验 → 代码构 option → emit)
                ↓
              END

设计分工:
- 代码(analyzer)只算事实:基数/求和/行数/样本(LLM 看不到全量,算不准)。
- LLM(decider)看事实+样本,自己判断每列含义,选 chart_type + 给字段映射。
- 代码(enforce_limits)用真实基数查可读性红线,违规降级;option_builder 按映射透视填数据。
- 因为 option 由代码构造、且有事实校验兜底,结构天然合法,无需 validate/correct 重试循环。
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.chart_agent import analyzer
from agent.chart_agent.analyzer import SUPPORTED_CHART_TYPES, enforce_limits
from agent.chart_agent.decider import decide_chart_type
from agent.chart_agent.option_builder import build_chart_option
from agent.chart_agent.templates import empty as empty_tpl
from agent.chart_agent.templates import error as error_tpl
from agent.chart_agent.templates import metric as metric_tpl
from agent.chart_agent.load_rows import load_rows
from agent.chart_agent.schemas import ChartAgentContext, ChartAgentState
from agent.schemas import WSStepInfo
from core.log import logger


# 行数超过此值就不画图(数据量大,可读性差),直接降级 table 展示全量。
CHART_MAX_ROWS = 200

# ── 用户点名图型:对话画图常见话术("画成折线图""换成饼图")────────────────
# 命中后优先尊重用户选择;LLM 选型只在用户没点名、或点名的画不出时生效。
_EXPLICIT_TYPE_WORDS: tuple[tuple[str, str], ...] = (
    ("堆叠", "stacked_bar"),
    ("折线", "line"), ("曲线", "line"),
    ("柱状", "bar"), ("柱图", "bar"), ("条形", "bar"),
    ("饼", "pie"), ("环形", "pie"),
    ("表格", "table"),
)
_TYPE_CN = {"line": "折线图", "bar": "柱状图", "pie": "饼图", "multi_line": "多系列折线图",
            "stacked_bar": "堆叠柱状图", "table": "表格"}
# 「点名类型 → 已算满足的类型」:LLM 选了多系列折线时,"折线"诉求已满足,不必强改单线
_SATISFIES = {"line": {"line", "multi_line"}, "bar": {"bar", "stacked_bar"},
              "pie": {"pie"}, "stacked_bar": {"stacked_bar"}, "table": {"table"}}


def _requested_type(query: str) -> str | None:
    for word, t in _EXPLICIT_TYPE_WORDS:
        if word in query:
            return t
    return None


def _build_for_requested(requested: str, target: str, field_map: dict,
                         shape, rows: list[dict[str, Any]], title: str) -> dict[str, Any]:
    """用户点名图型的统一出图(LLM 选型路径与换图快通道共用)。

    target 画得出(过 enforce_limits)→ 直接构图,只出点名的图(无切换项);
    画不出 → 不"静默换图":默认展示推荐的可生成图 + notice 说明原因与可切换项,
    什么图都画不出才落到数据表格。
    """
    final_t, req_reason = enforce_limits(target, field_map, shape)
    if final_t == target:
        if target == "table":
            config = _build_table_config(rows, title, "用户指定表格")
        else:
            config = build_chart_option(target, rows, field_map, title)
            config["compatible_types"] = [target]
            config["field_map"] = field_map
        return config

    drawable = _filter_compatible(list(SUPPORTED_CHART_TYPES), field_map, shape)
    why = (req_reason or "不满足该图型的数据要求").split(",降级")[0].split(",改用")[0]
    if drawable:
        # 默认直接展示推荐图(可生成列表的第一个),其余可生成图型 + 表格做切换项
        best = drawable[0]
        others = "、".join(_TYPE_CN.get(t, t) for t in drawable[1:] + ["table"])
        config = build_chart_option(best, rows, field_map, title)
        config["compatible_types"] = drawable + ["table"]
        config["field_map"] = field_map
        config["notice"] = (f"当前数据无法生成{_TYPE_CN[requested]}({why}),"
                            f"已改用{_TYPE_CN.get(best, best)}展示;可切换:{others}")
    else:
        config = _build_table_config(rows, title, f"用户指定{_TYPE_CN[requested]}画不出:{why}")
        config["notice"] = f"当前数据无法生成{_TYPE_CN[requested]}({why}),已展示数据表格"
    return config


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


async def analyze_data_shape(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="分析数据形状", status="running"))
    shape = analyzer.analyze(state.sql_result or [])
    writer(WSStepInfo(step="分析数据形状", status="success", data=shape.model_dump()))
    return {"data_shape": shape}


def _route_after_analyze(state: ChartAgentState) -> str:
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


# 切换项过滤:只保留"用同一份字段映射、在事实上确实画得出"的类型。
# 判定方式 = 跑一遍 enforce_limits,没被降级(返回原类型)才算这份数据能切到该类型。
# 例:工厂×产品类别(32 行)交叉表,pie/单维度图会被 enforce_limits 降级 → 不进切换菜单。
def _filter_compatible(types: list[str], field_map: dict, shape) -> list[str]:
    kept: list[str] = []
    for t in dict.fromkeys(types):  # 去重保序
        if t == "table":
            continue  # table 最后统一补
        final, _ = enforce_limits(t, field_map, shape)
        if final == t:
            kept.append(t)
    return kept


# 只采纳 LLM 给的、且在真实列里的映射;无效/缺失的字段不补规则(交给 enforce_limits 兜底降 table)。
def _resolve_field_map(decision, shape) -> dict:
    valid = {c.name for c in shape.columns} if shape else set()
    fm: dict = {}
    if decision.x_field in valid:
        fm["dimension"] = decision.x_field
    if decision.value_field in valid:
        fm["measure"] = decision.value_field
    if decision.series_field in valid:
        fm["series"] = decision.series_field
    if decision.value_fields:
        vf = [f for f in decision.value_fields if f in valid]
        if vf:
            fm["measures"] = vf
    return fm


# ── 核心:LLM 选型 + 给字段映射,option 由代码按映射构造 ─────────────────
async def build_chart(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    """LLM 决定 chart_type + 字段映射;option_builder 按映射用代码透视、填数据。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))

    rows = state.sql_result or []
    shape = state.data_shape
    query = state.messages[0].content if state.messages else "查询结果"
    # 标题用数据源头的问题(对话画图轮的 query 是"生成饼状图"这类指令,不能当标题);
    # query 本身仍用于点名图型识别与 LLM 选型意图
    title = _make_title(state.source_question or query)
    # 完整源头问题随 config 带给前端(元字段):标题会去前缀+截断,不可逆;
    # 前端的报告/再出图都需要"产生这份数据的原始问题",而图表指令轮
    # 最近的 user 消息是"生成折线图"这类指令,只能从这里拿
    src_question = str(state.source_question or query)
    requested = _requested_type(str(query))

    # ── 换图快通道:点名图型 + 上轮已有字段映射 → 零 LLM 直接构图 ────────────
    # "换成饼图/画成柱状图"这类请求作用在同一份数据上,列映射沿用上一轮即可,
    # 无需再让 LLM 判列选型;可行性与提示仍由 _build_for_requested 统一把关。
    prev_fm = (state.prev_chart_config or {}).get("field_map") or None
    if requested and prev_fm and shape:
        # 防御:映射列必须仍存在于当前数据(rows 与上轮图表同源,正常必一致)
        valid = {c.name for c in shape.columns}
        fm_cols: set[str] = set()
        for v in prev_fm.values():
            fm_cols.update(v if isinstance(v, list) else [v])
        if fm_cols and fm_cols <= valid:
            try:
                # 上轮映射带分组列时,"折线/柱状"诉求升级为多系列变体,保住分组信息
                target = ({"line": "multi_line", "bar": "stacked_bar"}.get(requested, requested)
                          if prev_fm.get("series") else requested)
                config = _build_for_requested(requested, target, prev_fm, shape, rows, title)
                config["source_question"] = src_question
                logger.info(f"换图快通道(零 LLM):{requested} → {config.get('chart_type')}")
                writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
                return {"chart_config": config}
            except Exception as exc:
                # 快通道任何意外 → 回退正常 LLM 选型链,不影响出图
                logger.warning(f"换图快通道失败,回退 LLM 选型:{exc}")

    # 整段构图包一层 try/except:任何意外异常都不让它冒出去断流,
    # 一律兜底成表格,保证图表分支永远能给前端一个可渲染的结果。
    try:
        # LLM 看"事实清单 + 样本数据",自己判断每列含义,从全部支持类型里选型 + 给映射 + 给可切换类型。
        decision = await decide_chart_type(str(query), shape, rows[:8], SUPPORTED_CHART_TYPES)

        if decision is None:
            # LLM 调用失败 → 直接降级表格(不再用规则兜底)
            config = _build_table_config(rows, title, "LLM 选型失败,降级为表格")
            chart_type, reason = "table", "LLM 选型失败"
        else:
            chart_type = decision.chart_type if decision.chart_type in SUPPORTED_CHART_TYPES else "table"
            field_map = _resolve_field_map(decision, shape)
            reason = decision.reason

            # ── 用户点名了图表类型(如"画成折线图""换成饼图")────────────────
            #   统一交给 _build_for_requested:画得出 → 只出点名的图(无切换项);
            #   画不出 → 默认推荐图 + notice 提示原因与可切换项,绝不静默换图。
            #   注意:LLM 顺着用户点名选了该类型、但 enforce_limits 判画不出的情况,
            #   也走这里 —— 否则会被后面的兜底校验静默降级,用户以为生成错图。
            if requested:
                # 目标类型:LLM 已选了满足诉求的类型(如点名"折线"它选了 multi_line)就用它,
                # 否则用点名类型本身。
                target = chart_type if chart_type in _SATISFIES.get(requested, {requested}) else requested
                config = _build_for_requested(requested, target, field_map, shape, rows, title)
                config["source_question"] = src_question
                logger.info(f"图表生成(点名{_TYPE_CN[requested]}):type={config.get('chart_type')}"
                            f"{',notice:' + config['notice'] if config.get('notice') else ''}")
                writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
                return {"chart_config": config}

            # 代码兜底校验:用真实基数查硬限制(扇区/柱子/系列数上限、必需映射齐不齐),违反则降级(可能降到 table)。
            # 只查精确事实,不做语义猜测。LLM 映射无效/缺失时,这里也会因"缺必需映射"降级表格。
            chart_type, limit_reason = enforce_limits(chart_type, field_map, shape)
            if limit_reason:
                reason = f"{reason}｜{limit_reason}"

            if chart_type == "table":
                config = _build_table_config(rows, title, reason)
            else:
                config = build_chart_option(chart_type, rows, field_map, title)
                # 可切换类型:LLM 给的 → 过滤为支持类型 → 再用 enforce_limits 滤掉这份数据画不出的,
                # 保证含当前类型 + table 兜底。避免给出"切过去就是一坨"的选项(如交叉表切饼图)。
                compat = [t for t in (decision.compatible_types or []) if t in SUPPORTED_CHART_TYPES]
                compat = _filter_compatible(compat, field_map, shape)
                if chart_type not in compat:
                    compat = [chart_type] + compat
                if "table" not in compat:
                    compat.append("table")
                config["compatible_types"] = compat
                config["field_map"] = field_map

        logger.info(f"图表生成(LLM 判列+选型+映射+切换项, 代码填数据+校验):type={chart_type}, reason={reason}")
    except Exception as exc:
        # 选型/校验/构图任意环节的意外异常 → 兜底表格(_build_table_config 是纯代码,安全)
        logger.exception(f"build_chart 异常,兜底表格:{exc}")
        config = _build_table_config(rows, title, f"图表生成异常,降级为表格:{exc}")

    config["source_question"] = src_question
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


# ── 3 个状态卡(deterministic,不调 LLM)─────────────────────────────────
async def render_error(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    config = error_tpl.render(error_message=state.error or "未知错误", original_sql=state.sql)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def render_empty(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    config = empty_tpl.render()
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def render_metric(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    rows = state.sql_result or []
    query = state.source_question or (state.messages[0].content if state.messages else "")
    title = str(query)[:50] or "结果"
    config = metric_tpl.render(title=title, rows=rows)
    config["source_question"] = str(query)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


async def render_table(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    """行数 > CHART_MAX_ROWS:数据太多,直接表格展示全量数据。"""
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成图表", status="running"))
    rows = state.sql_result or []
    query = state.source_question or (state.messages[0].content if state.messages else "查询结果")
    config = _build_table_config(
        rows, title=str(query)[:50],
        reason=f"结果 {len(rows)} 行 > {CHART_MAX_ROWS},数据量大,降级为表格",
    )
    config["source_question"] = str(query)
    writer(WSStepInfo(step="生成图表", status="success", data=config, finish=True))
    return {"chart_config": config}


def _route_after_load(state: ChartAgentState) -> str:
    # 有数据(空数组也算,走 render_empty)或带上游错误(走 render_error)→ 进形状分析;
    # 取数失败(load_rows 已发说明卡)→ 直接结束。
    if state.sql_result is not None or state.error:
        return "analyze_data_shape"
    return END


def _build():
    sg = StateGraph(state_schema=ChartAgentState, context_schema=ChartAgentContext)

    sg.add_node("load_rows", load_rows)
    sg.add_node("analyze_data_shape", analyze_data_shape)
    sg.add_node("build_chart", build_chart)
    sg.add_node("render_table", render_table)
    sg.add_node("render_error", render_error)
    sg.add_node("render_empty", render_empty)
    sg.add_node("render_metric", render_metric)

    sg.add_edge(START, "load_rows")
    sg.add_conditional_edges(
        "load_rows",
        _route_after_load,
        {"analyze_data_shape": "analyze_data_shape", END: END},
    )
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
