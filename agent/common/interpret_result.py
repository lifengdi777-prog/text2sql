"""解读节点：把查询结果翻译成一段自然语言描述 + 克制洞察。

与 generate_chart 并行运行，只依赖 sql_result。

防幻觉策略：
- 结果行数 ≤ FULL_ROWS_THRESHOLD → 全量喂 LLM
- 否则 → 用 Python 算好 sum/max/min/avg/top-N 喂给 LLM，让它照着说不用自己算
"""
from __future__ import annotations

from typing import Any

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.chart_agent import analyzer
from agent.llm import llm
from agent.prompts import load_prompt
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


# 行数 ≤ 此值则全量喂；否则喂统计量（避免采样漏极值导致 LLM 说错）
FULL_ROWS_THRESHOLD = 50
TOP_N = 10


def _compute_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """大结果集：用 Python 算确定性统计量，LLM 只负责表述、不负责计算。"""
    shape = analyzer.analyze(rows)
    numeric_cols = [c.name for c in shape.columns if c.semantic_type == "numeric"]

    numeric_summary: dict[str, Any] = {}
    for col in numeric_cols:
        vals = [r[col] for r in rows if isinstance(r.get(col), (int, float))]
        if vals:
            numeric_summary[col] = {
                "sum": sum(vals),
                "max": max(vals),
                "min": min(vals),
                "avg": round(sum(vals) / len(vals), 2),
            }

    # 按第一个数值列取 top-N，供 LLM 引用“最高的是谁”
    top_rows: list[dict[str, Any]] = []
    if numeric_cols:
        key = numeric_cols[0]
        top_rows = sorted(
            rows,
            key=lambda r: r[key] if isinstance(r.get(key), (int, float)) else float("-inf"),
            reverse=True,
        )[:TOP_N]

    return {
        "row_count": len(rows),
        "numeric_summary": numeric_summary,
        f"top_{TOP_N}_rows": top_rows,
    }


def _build_data_payload(rows: list[dict[str, Any]]) -> str:
    if len(rows) <= FULL_ROWS_THRESHOLD:
        return f"(共 {len(rows)} 行，全量)\n{rows}"
    stats = _compute_stats(rows)
    return f"(共 {len(rows)} 行，数据量较大，以下为程序算好的统计摘要)\n{stats}"


async def interpret_result(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer

    # SQL 报错 / 无结果 → 没有可解读的数据，跳过（不发事件，前端也就不渲染解读块）
    if state.error or not state.sql_result:
        return {"interpretation": None}

    writer(WSStepInfo(step="数据解读", status="running"))

    query = state.messages[0].content if state.messages else ""
    rows = state.sql_result
    data_payload = _build_data_payload(rows)

    prompt = await load_prompt("interpret_result")
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=(
            f"用户问题：{query}\n\n"
            f"执行的 SQL：{state.sql}\n\n"
            f"数据：\n{data_payload}"
        )),
    ]

    accumulated = ""
    try:
        # 流式：每来一段 token 就把“累计文本”推给前端，前端替换显示 → 逐字蹦
        async for chunk in llm.astream(messages):
            delta = chunk.content
            if not isinstance(delta, str) or not delta:
                continue
            accumulated += delta
            writer(WSStepInfo(step="数据解读", status="running", data=accumulated))

        # 结果被行数上限截断 → 末尾追加一句诚实提示(只在真截断时加)
        if state.truncated:
            from agent.db_agent.nodes.validate_sql import MAX_RESULT_ROWS
            accumulated += f"\n\n(注:结果行数较多,仅展示并解读前 {MAX_RESULT_ROWS} 行。)"

        logger.info(f"数据解读：{accumulated}")
        # 收尾事件：全文 = 最后一个 running 的累计文本，前端替换不会重复
        writer(WSStepInfo(step="数据解读", status="success", data=accumulated))
        return {"interpretation": accumulated}

    except Exception as exc:
        logger.exception(f"数据解读失败：{exc}")
        writer(WSStepInfo(step="数据解读", status="error", data={"error": str(exc)}))
        return {"interpretation": None}
