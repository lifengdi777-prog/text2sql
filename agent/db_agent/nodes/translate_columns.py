"""结果列名翻译节点:把英文列名翻成中文,改写结果集的列 key。

放在 execute_sql 之后、generate_chart / interpret_result 并行之前:
改一次 key,图表(轴名/图例/表头)和数据解读就全是中文,且彼此一致。
SQL 本身保持英文物理列名不变,中文只是展示层的事。

容错:翻译失败 / SQL 报错无结果 / 列名本就是中文 → 原样跳过,不阻断主流程。
"""
from __future__ import annotations

import re
from typing import Any

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel

from agent.llm import llm
from agent.prompts import load_prompt
from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from core.log import logger


_MAX_RETRY = 2   # json_mode 偶发吐空,失败时重试的总次数


class _ColumnLabels(BaseModel):
    labels: dict[str, str]


def _needs_translation(columns: list[str]) -> bool:
    # 含英文字母才需要翻译;列名本就是中文别名时直接跳过,省一次 LLM 调用
    return any(re.search(r"[A-Za-z]", c) for c in columns)


def _build_label_map(columns: list[str], raw: dict[str, str]) -> dict[str, str]:
    """把 LLM 给的映射收敛成"每列一个唯一中文标签",缺失/重复都回退英文原名。"""
    used: set[str] = set()
    final: dict[str, str] = {}
    for c in columns:
        label = (raw.get(c) or "").strip() or c
        if label in used:
            label = c  # 英文原名天然唯一,避免两列撞成同名 key 把数据吃掉
        used.add(label)
        final[c] = label
    return final


async def translate_columns(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer

    # SQL 报错 / 无结果 → 没有列可翻译,直接跳过
    if state.error or not state.sql_result:
        return {}

    rows = state.sql_result
    columns = list(rows[0].keys())
    if not _needs_translation(columns):
        return {}

    writer(WSStepInfo(step="翻译列名", status="running"))

    query = state.messages[0].content if state.messages else ""
    prompt = await load_prompt("translate_columns")
    structured_llm = llm.with_structured_output(_ColumnLabels, method="json_mode")

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=(
            f"用户问题:{query}\n\n"
            f"执行的 SQL:{state.sql}\n\n"
            f"列名列表:{columns}\n\n"
            f"请输出 labels JSON。"
        )),
    ]

    # json_mode 偶发吐空/解析失败,重试一次再回退;失败不阻断主流程(保留英文原名)。
    out: _ColumnLabels | None = None
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            out = await structured_llm.ainvoke(messages)  # type: ignore
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(f"列名翻译第 {attempt} 次失败:{exc}")

    if out is None:
        logger.error(f"列名翻译重试 {_MAX_RETRY} 次仍失败,保留英文原名:{last_exc}")
        writer(WSStepInfo(step="翻译列名", status="error", data={"error": str(last_exc)}))
        return {}

    label_map = _build_label_map(columns, out.labels)

    renamed: list[dict[str, Any]] = [
        {label_map[c]: r.get(c) for c in columns} for r in rows
    ]
    logger.info(f"列名翻译:{label_map}")
    # finish=True 再发一次结果数组:让前端表格(老协议的 result)也换成中文 key,
    # 与图表轴名/表头保持一致(前端对任意 finish+数组事件都会覆盖 result)。
    writer(WSStepInfo(step="翻译列名", status="success", data=renamed, finish=True))
    return {"sql_result": renamed}
