"""图表决策:LLM 看"事实清单 + 样本数据",自己判断每列含义,选 chart_type + 给字段映射。

分工:
- 代码(analyzer)只算**事实**:每列的基数/求和/样本、总行数——这些是 LLM 看不到全量、算不准的。
- LLM 拿着事实 + 几行真实数据,**自己判断每列是时间/分类/数值**,再选型 + 给映射。
- 数据本身(透视、排序、填 series.data)由 option_builder 用代码做。
"""
from __future__ import annotations

from typing import Any

from langchain.messages import HumanMessage, SystemMessage

from agent.chart_agent.schemas import ChartTypeDecision, DataShape
from agent.llm import fast_llm
from agent.prompts import load_prompt
from core.log import logger


def _facts(shape: DataShape) -> list[dict[str, Any]]:
    """把每列压成一张"事实清单"(代码精确统计的硬数字,不含语义猜测)。"""
    out: list[dict[str, Any]] = []
    for c in shape.columns:
        item: dict[str, Any] = {
            "列名": c.name,
            "数据类型": c.dtype,
            "不同值个数": c.cardinality,
            "样本值": c.sample,
        }
        if c.min_value is not None:  # 数值列才有
            item.update({"最小值": c.min_value, "最大值": c.max_value, "求和": c.sum_value})
        out.append(item)
    return out


async def decide_chart_type(
    query: str,
    shape: DataShape,
    sample_rows: list[dict[str, Any]],
    supported: list[str],
) -> ChartTypeDecision | None:
    """LLM 选 chart_type(从 supported 全集里)+ 给字段映射。

    失败返回 None,由上层用规则兜底。chart_type 越界、映射列名是否有效、可读性硬限制,
    均由上层(enforce_limits 等)再校验。
    """
    prompt = await load_prompt("chart_type_picker")
    structured_llm = fast_llm.with_structured_output(ChartTypeDecision, method="json_mode")

    user_msg = (
        f"用户问题:{query}\n\n"
        f"总行数:{shape.row_count}\n\n"
        f"各列事实清单(代码精确统计;字段映射只能从这些「列名」里选):\n{_facts(shape)}\n\n"
        f"前几行真实数据(据此判断每列含义与列间关系):\n{sample_rows}\n\n"
        f"前端支持的图表类型(chart_type 只能从中选恰好一个):{supported}\n\n"
        f"请自行判断每列角色,选出最贴合用户意图的 chart_type,并给出字段映射与 reason。"
    )
    try:
        decision: ChartTypeDecision = await structured_llm.ainvoke([  # type: ignore
            SystemMessage(content=prompt),
            HumanMessage(content=user_msg),
        ])
        return decision
    except Exception as exc:
        logger.exception(f"图表选型异常,将由上层用规则兜底:{exc}")
        return None
