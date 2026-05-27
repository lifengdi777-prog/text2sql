"""校验 LLM 产出的 ECharts spec 是否合法、是否能渲染。

校验失败时返回 issues 列表,subgraph 路由到 correct_spec;
通过则把 spec 转成最终 chart_config 进 END。
"""
from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from agent.chart_agent.analyzer import chart_field_map, compatible_chart_types
from agent.chart_agent.schemas import EChartsSpec
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


# 不同图表类型的人为合理性上限
_PIE_MAX_SLICES = 10
_BAR_MAX_BARS = 30


def _validate_field_refs(spec: EChartsSpec, rows: list[dict[str, Any]]) -> list[str]:
    """检查 xAxis.data 里的值确实出现在 rows 的某列里。

    LLM 自由发挥时容易把数据搞错(比如月份写成 "Jan/Feb" 而源数据是 1/2),
    这一步把这种错抓出来。
    """
    if not rows or not spec.xAxis:
        return []
    issues: list[str] = []
    x_data = spec.xAxis.get("data") if isinstance(spec.xAxis, dict) else None
    if not isinstance(x_data, list) or not x_data:
        return []
    # 把 rows 里所有列的值合并成一个 set,xAxis.data 必须是这个 set 的子集
    all_values: set[Any] = set()
    for r in rows:
        for v in r.values():
            try:
                all_values.add(v)
            except TypeError:
                continue
    missing = [str(v) for v in x_data if v not in all_values and str(v) not in {str(x) for x in all_values}]
    if missing:
        issues.append(f"xAxis.data 中以下值在 rows 里找不到:{missing[:5]}(可能是 LLM 编造的标签)")
    return issues


def _validate_series_lengths(spec: EChartsSpec) -> list[str]:
    """非饼图:每个 series.data 长度必须 == xAxis.data 长度。"""
    if spec.chart_type == "pie" or not spec.xAxis or not spec.series:
        return []
    issues: list[str] = []
    x_data = spec.xAxis.get("data") if isinstance(spec.xAxis, dict) else None
    if not isinstance(x_data, list):
        return []
    n = len(x_data)
    for i, s in enumerate(spec.series):
        data = s.get("data")
        if not isinstance(data, list):
            issues.append(f"series[{i}].data 必须是 list,当前是 {type(data).__name__}")
            continue
        if len(data) != n:
            issues.append(
                f"series[{i}].data 长度 {len(data)} != xAxis.data 长度 {n},"
                f"长表透视后请补 null(line)或 0(stacked_bar)对齐"
            )
    return issues


def _validate_chart_specific(spec: EChartsSpec, rows: list[dict[str, Any]]) -> list[str]:
    """按 chart_type 各自的硬规则校验。"""
    issues: list[str] = []
    ct = spec.chart_type

    if ct == "pie":
        # pie 不该有 xAxis/yAxis
        if spec.xAxis or spec.yAxis:
            issues.append("pie 图表不应该有 xAxis 或 yAxis 字段,请删除")
        # series 整个为空时 backfill 也无能为力(没东西可补),必须拦下重做
        if not spec.series:
            issues.append("pie series 不能为空")
        # series[0].data 必须是 [{name, value}]
        if spec.series:
            data = spec.series[0].get("data", [])
            if not isinstance(data, list) or not all(
                isinstance(item, dict) and "name" in item and "value" in item
                for item in data
            ):
                issues.append("pie series[0].data 必须是 [{name, value}, ...] 对象数组")
            elif len(data) > _PIE_MAX_SLICES:
                issues.append(
                    f"pie 扇区数 {len(data)} 超过 {_PIE_MAX_SLICES},视觉上会糊,请改用 bar"
                )

    elif ct in ("line", "bar", "multi_line", "stacked_bar"):
        if not spec.xAxis or not spec.yAxis:
            issues.append(f"{ct} 必须有 xAxis 和 yAxis 字段")
        if not spec.series:
            issues.append(f"{ct} series 不能为空")
        # bar/stacked_bar 横轴密度检查
        if ct in ("bar", "stacked_bar") and spec.xAxis:
            x_data = spec.xAxis.get("data") if isinstance(spec.xAxis, dict) else None
            if isinstance(x_data, list) and len(x_data) > _BAR_MAX_BARS:
                issues.append(f"{ct} 横轴 {len(x_data)} 项超过 {_BAR_MAX_BARS},建议改 table")
        # stacked_bar 所有 series 必须有相同的 stack 名
        if ct == "stacked_bar" and len(spec.series) >= 2:
            stacks = {s.get("stack") for s in spec.series}
            if len(stacks) > 1 or None in stacks:
                issues.append(
                    f"stacked_bar 所有 series 必须有相同的 stack 字段,当前 stack 值:{stacks}"
                )
        # series.type 必须跟 chart_type 对齐
        expected_type = {
            "line": "line", "multi_line": "line",
            "bar": "bar", "stacked_bar": "bar",
        }[ct]
        for i, s in enumerate(spec.series):
            if s.get("type") != expected_type:
                issues.append(
                    f"series[{i}].type 应该是 '{expected_type}',当前是 '{s.get('type')}'"
                )

    return issues


def _run_all_checks(spec: EChartsSpec, rows: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    issues += _validate_chart_specific(spec, rows)
    issues += _validate_series_lengths(spec)
    issues += _validate_field_refs(spec, rows)
    return issues


async def validate_spec(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="校验图表配置", status="running"))

    spec = state.chart_spec
    rows = state.sql_result or []

    if spec is None:
        # generate 阶段就崩了,直接走 correct
        issues = [state.chart_error or "spec 为 None,需要重新生成"]
        writer(WSStepInfo(step="校验图表配置", status="error", data={"issues": issues}))
        return {"chart_issues": issues}

    issues = _run_all_checks(spec, rows)

    if not issues:
        # 通过:把 spec 转成最终 chart_config
        config = spec.to_echarts_option()
        # table 类型在这里补齐 columns/rows(让 LLM 偷懒只出 chart_type + title)
        if spec.chart_type == "table" and rows:
            columns = list(rows[0].keys())
            config["columns"] = columns
            config["rows"] = [[r.get(c) for c in columns] for r in rows]
            config["row_count"] = len(rows)
        # 注入"兼容类型集 + 字段映射",供前端切换菜单 + 本地构图(确定性,无 LLM)
        compat = compatible_chart_types(state.data_shape)
        # 兜底:LLM 实际选的类型一定要在集合里(否则前端切不回当前图)
        if spec.chart_type not in compat:
            compat = [spec.chart_type] + compat
        config["compatible_types"] = compat
        config["field_map"] = chart_field_map(state.data_shape)
        logger.info(f"图表 spec 校验通过:type={spec.chart_type}")
        writer(WSStepInfo(step="校验图表配置", status="success",
                          data={"chart_type": spec.chart_type}))
        # 同步清掉 issues,防止上轮残留
        return {"chart_config": config, "chart_issues": []}

    logger.warning(f"图表 spec 校验失败:{issues}")
    writer(WSStepInfo(step="校验图表配置", status="error", data={"issues": issues}))
    return {"chart_issues": issues}
