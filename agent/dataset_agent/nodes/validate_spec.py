"""校验 LLM 产出的 ComputeSpec:sheet / 列名是否真实存在。

定位:夹在 generate_spec 和 execute_spec 之间,是「可靠的规则裁判」——
不调 LLM,纯按真实 schema 做确定性校验:

  1. sheet 是否存在;
  2. filters / groupby / aggregations / order_by 引用的列是否存在。

近似拼错(如「工厂名」→「工厂」)用 difflib 自动纠正、就地改写 spec,
**不计入 issues**(这是省掉一半 LLM 调用的关键);改不动的(找不到近似项)
才计入 spec_issues,交给 correct_spec 让 LLM 重做。

order_by 比较特殊:它引用的是「聚合之后的结果列」(groupby 列名 + 聚合 alias),
所以单独按结果列集合校验,避免误报。
"""
from __future__ import annotations

import copy
import difflib
from typing import Any

from langgraph.runtime import Runtime

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.schemas import WSStepInfo
from core.log import logger
from services.dataset_loader import get_dataset_info


# difflib 相似度阈值:0.6 是默认值,够稳(太低会乱纠正,太高放过不了拼写小错)
_CUTOFF = 0.6


def _extract_schema_columns(schema: dict[str, Any]) -> dict[str, list[str]]:
    """schema_json → {sheet_name: [真实列名, ...]}。"""
    out: dict[str, list[str]] = {}
    for sheet_name, sheet_info in (schema.get("sheets") or {}).items():
        cols = [c.get("name", "") for c in sheet_info.get("columns", []) if c.get("name")]
        out[sheet_name] = cols
    return out


def _closest(name: Any, candidates: list[str]) -> str | None:
    """在 candidates 里找跟 name 最接近的一个;找不到(相似度不够)返回 None。"""
    if not name or not candidates:
        return None
    matches = difflib.get_close_matches(str(name), [str(c) for c in candidates], n=1, cutoff=_CUTOFF)
    return matches[0] if matches else None


def _alias_for(agg: dict[str, Any]) -> str:
    """跟 services/compute_spec.py 的 _alias_for 保持一致:alias 优先,否则 {col}_{func}。"""
    return agg.get("alias") or f"{agg.get('col')}_{agg.get('func')}"


def _result_columns(spec: dict[str, Any], valid_cols: list[str]) -> list[str]:
    """聚合后 DataFrame 的列集合(order_by 应该针对它校验)。

    与 compute_spec.py 的执行语义对齐:
      - 有 groupby 或 aggregations → 结果列 = groupby 列 + 聚合 alias;
      - 都没有(纯过滤)→ 结果列 = 原始列。
    """
    groupby = spec.get("groupby") or []
    aggs = spec.get("aggregations") or []
    if aggs or groupby:
        return list(groupby) + [_alias_for(a) for a in aggs]
    return list(valid_cols)


def _check_col(
    col: Any,
    valid_cols: list[str],
    where: str,
    fixes: list[str],
    issues: list[str],
) -> str | None:
    """校验单个列引用。存在→原样返回;能 difflib 纠正→记 fix 返回新名;否则记 issue 返回 None。"""
    if not col or col in valid_cols:
        return col
    match = _closest(col, valid_cols)
    if match:
        fixes.append(f"{where} 列 '{col}' → '{match}'")
        return match
    issues.append(f"{where} 引用的列 '{col}' 不存在;可用列:{valid_cols}")
    return None


async def validate_spec(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="校验计算方案", status="running"))

    # 前序已出错 / 没 spec / 没 dataset_id:不在这里拦,交给下游节点处理
    if state.error:
        return {}
    if state.compute_spec is None:
        issues = ["compute_spec 为空,需要重新生成"]
        writer(WSStepInfo(step="校验计算方案", status="error", data={"issues": issues}))
        return {"spec_issues": issues}
    if state.dataset_id is None:
        return {"spec_issues": []}

    info = await get_dataset_info(state.dataset_id)
    if not info or not info.get("schema"):
        # 拿不到真实 schema → 没法校验,放行(execute_spec 会再校验/报错)
        return {"spec_issues": []}

    sheet_cols = _extract_schema_columns(info["schema"])
    valid_sheets = list(sheet_cols.keys())

    spec = copy.deepcopy(state.compute_spec)  # 深拷贝,准备就地修正后整体写回
    issues: list[str] = []
    fixes: list[str] = []

    # 1) sheet
    sheet = spec.get("sheet")
    if sheet not in sheet_cols:
        match = _closest(sheet, valid_sheets)
        if match:
            fixes.append(f"sheet '{sheet}' → '{match}'")
            spec["sheet"] = match
            sheet = match
        else:
            issues.append(f"sheet '{sheet}' 不存在;可用 sheet:{valid_sheets}")
            # sheet 都定不下来 → 没法校验列,直接把问题抛给 correct_spec
            writer(WSStepInfo(step="校验计算方案", status="error", data={"issues": issues}))
            return {"compute_spec": spec, "spec_issues": issues}

    valid_cols = sheet_cols.get(sheet, [])

    # 2) filters[].col
    for f in spec.get("filters") or []:
        fixed = _check_col(f.get("col"), valid_cols, "filter", fixes, issues)
        if fixed is not None:
            f["col"] = fixed

    # 3) groupby[]
    groupby = spec.get("groupby")
    if groupby:
        new_groupby = []
        for col in groupby:
            fixed = _check_col(col, valid_cols, "groupby", fixes, issues)
            new_groupby.append(fixed if fixed is not None else col)
        spec["groupby"] = new_groupby

    # 4) aggregations[].col(count(*) 的 "*" 跳过)
    for a in spec.get("aggregations") or []:
        col = a.get("col")
        if col == "*":
            continue
        fixed = _check_col(col, valid_cols, "aggregation", fixes, issues)
        if fixed is not None:
            a["col"] = fixed

    # 5) order_by[].col —— 针对聚合后的结果列校验
    result_cols = _result_columns(spec, valid_cols)
    for o in spec.get("order_by") or []:
        col = o.get("col")
        if not col or col in result_cols:
            continue
        match = _closest(col, result_cols)
        if match:
            fixes.append(f"order_by 列 '{col}' → '{match}'")
            o["col"] = match
        else:
            issues.append(
                f"order_by 引用的列 '{col}' 不存在(可能 alias 没对上);可用:{result_cols}"
            )

    if fixes:
        logger.info(f"ComputeSpec 自动纠正:{fixes}")
    if issues:
        logger.warning(f"ComputeSpec 校验未通过:{issues}")
        writer(WSStepInfo(step="校验计算方案", status="error", data={"issues": issues, "fixes": fixes}))
        return {"compute_spec": spec, "spec_issues": issues}

    # 通过(可能含 difflib 自动纠正)→ 清空 issues,带着修正后的 spec 去执行
    writer(WSStepInfo(step="校验计算方案", status="success", data={"fixes": fixes}))
    return {"compute_spec": spec, "spec_issues": []}
