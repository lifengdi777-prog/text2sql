"""LLM 出 ComputeSpec 的节点。

输入(从 state 拼 prompt):
  - 用户问题(state.messages[0])
  - 数据集 schema markdown(state.rendered_schema)
  - ES 召回的真实值(state.value_hits)
  - 业务指标定义(skills/business_metrics.md,进程级缓存)

输出:
  - state.compute_spec(dict,直接序列化的 ComputeSpec)
"""
from __future__ import annotations

from pathlib import Path

from langchain.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.llm import llm
from agent.prompts import load_prompt
from agent.schemas import WSStepInfo
from core.log import logger
from services.compute_spec import ComputeSpec


# ───────── 静态资产加载(进程级缓存)─────────

_PROMPT_REL = Path("agent/dataset_agent/prompts/compute_spec_generator.md")
_METRICS_REL = Path("skills/business_metrics.md")

_PROMPT_CACHE: str | None = None
_METRICS_CACHE: str | None = None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _get_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _read_text(_PROMPT_REL)
    return _PROMPT_CACHE


def _get_metrics() -> str:
    global _METRICS_CACHE
    if _METRICS_CACHE is None:
        if _METRICS_REL.exists():
            _METRICS_CACHE = _read_text(_METRICS_REL)
        else:
            _METRICS_CACHE = "(暂无业务指标定义)"
    return _METRICS_CACHE


# ───────── value_hits 格式化 ─────────

def _format_value_hits(hits: list[dict]) -> str:
    """召回值渲染成 LLM 看的 markdown。"""
    if not hits:
        return "(本次问题无 ES 召回结果)"
    lines = ["格式:- [sheet] col = value"]
    for h in hits:
        lines.append(f"- [{h.get('sheet','?')}] {h.get('col','?')} = {h.get('value','?')}")
    return "\n".join(lines)


# ───────── 节点 ─────────

async def generate_spec(state: DatasetAgentState, runtime: Runtime[DatasetAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="生成计算方案", status="running"))

    if state.error:
        # 前序步骤已出错,直接跳过
        return {}

    query = state.messages[0].content if state.messages else ""
    if not query:
        msg = "用户问题为空"
        writer(WSStepInfo(step="生成计算方案", status="error", data={"error": msg}))
        return {"error": msg}

    system_prompt = _get_prompt()
    metrics_md = _get_metrics()
    hits_md = _format_value_hits(state.value_hits)

    user_content = (
        f"# 数据集 Schema\n"
        f"{state.rendered_schema}\n\n"
        f"# 业务指标定义(参考用 —— **先看 schema 有没有同名列**,有就直接用)\n"
        f"{metrics_md}\n\n"
        f"# 用户问题里疑似涉及的真实值(ES 召回,若有 → 写 filter 优先用这些精确值)\n"
        f"{hits_md}\n\n"
        f"# 用户问题\n"
        f"{query}\n\n"
        f"请输出 ComputeSpec JSON。**写之前,确认所有 col 都是 schema 里真实存在的列**。"
    )

    structured = llm.with_structured_output(ComputeSpec, method="json_mode")
    try:
        spec: ComputeSpec = await structured.ainvoke([  # type: ignore
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])
        spec_dict = spec.model_dump()
        logger.info(f"ComputeSpec 生成:sheet={spec.sheet} reason={spec.reason}")
        writer(WSStepInfo(
            step="生成计算方案",
            status="success",
            data={
                "sheet": spec.sheet,
                "filters": spec_dict.get("filters", []),
                "groupby": spec_dict.get("groupby", []),
                "aggregations": spec_dict.get("aggregations", []),
                "order_by": spec_dict.get("order_by", []),
                "limit": spec_dict.get("limit"),
                "reason": spec.reason,
            },
        ))
        return {"compute_spec": spec_dict}

    except Exception as exc:
        logger.exception(f"ComputeSpec 生成失败:{exc}")
        writer(WSStepInfo(step="生成计算方案", status="error", data={"error": str(exc)}))
        return {"error": f"生成计算方案失败:{exc}"}
