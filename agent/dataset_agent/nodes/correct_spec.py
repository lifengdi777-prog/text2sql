"""根据 validate_spec 报的 issues,让 LLM 重做 ComputeSpec。

定位:「带着裁判报告重做」——只有 difflib 自动纠正不了的列名/sheet 错误才会走到这。
喂给 LLM:真实 schema + 上一版 spec + 校验报告 + 用户原问题,让它产出修正版。
最多重试 MAX_RETRY 次(spec_retry_count 计数封顶),用尽后由 graph 路由到兜底执行。

复用 generate_spec 的同一份 system prompt(里面有写 spec 的完整规则),
只是把 user content 换成「修正语境」。
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.dataset_agent.nodes import latest_user_query
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.llm import llm
from agent.schemas import WSStepInfo
from core.log import logger
from services.compute_spec import ComputeSpec


MAX_RETRY = 2  # 最多让 LLM 重做 2 次,避免死循环

_PROMPT_REL = Path("agent/dataset_agent/prompts/compute_spec_generator.md")
_PROMPT_CACHE: str | None = None


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_REL.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def _build_correct_message(
    query: str,
    rendered_schema: str,
    last_spec_json: str,
    issues: list[str],
) -> str:
    issues_text = "\n".join(f"- {x}" for x in issues)
    return (
        f"# 数据集 Schema\n{rendered_schema}\n\n"
        f"# 你上一轮产出的 ComputeSpec(有问题)\n{last_spec_json}\n\n"
        f"# 校验报告(请逐条修复 —— 下列 sheet/列在 schema 里查无此名)\n{issues_text}\n\n"
        f"# 用户原始问题\n{query}\n\n"
        f"请输出修正后的**完整** ComputeSpec JSON。"
        f"所有 sheet 和 col 必须严格使用上面 schema 中真实存在的名字。"
    )


async def correct_spec(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    retry_count = state.spec_retry_count + 1
    writer(WSStepInfo(step=f"修正计算方案(第 {retry_count} 次)", status="running"))

    query = latest_user_query(state.messages) or ""
    issues = state.spec_issues or []
    last_spec_json = (
        json.dumps(state.compute_spec, ensure_ascii=False, indent=2)
        if state.compute_spec else "{}"
    )

    system_prompt = _get_prompt()
    structured = llm.with_structured_output(ComputeSpec, method="json_mode")

    try:
        new_spec: ComputeSpec = await structured.ainvoke([  # type: ignore
            SystemMessage(content=system_prompt),
            HumanMessage(content=_build_correct_message(
                str(query), state.rendered_schema, last_spec_json, issues,
            )),
        ])
        spec_dict = new_spec.model_dump()
        logger.info(f"ComputeSpec 第 {retry_count} 次修正:sheet={new_spec.sheet} reason={new_spec.reason}")
        writer(WSStepInfo(
            step=f"修正计算方案(第 {retry_count} 次)",
            status="success",
            data={"sheet": new_spec.sheet, "reason": new_spec.reason},
        ))
        return {"compute_spec": spec_dict, "spec_retry_count": retry_count}

    except Exception as exc:
        # 修正本身崩了:计数照样 +1,回到 validate 会再判一次,达上限即兜底执行
        logger.exception(f"ComputeSpec 修正异常:{exc}")
        writer(WSStepInfo(
            step=f"修正计算方案(第 {retry_count} 次)",
            status="error",
            data={"error": str(exc)},
        ))
        return {"spec_retry_count": retry_count}
