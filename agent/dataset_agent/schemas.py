"""数据集分析 graph 的 State / Context schema。

继承 WSAgentState,这样 chart_agent / interpret_result 这些节点能原样复用
(它们读 state.sql_result / state.messages,字段都在父类里)。
额外字段是 dataset_agent 专用的中间状态。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.schemas import WSAgentState


class DatasetAgentState(WSAgentState):
    """Excel 数据分析 graph 的运行时状态。"""
    # 请求级输入
    dataset_id: int | None = None
    # load_schema 节点填:LLM 可读的 markdown 形式 schema
    rendered_schema: str = ""
    # recall_values 节点填:ES 命中的真实值 [{sheet, col, value}, ...]
    value_hits: list[dict[str, Any]] = []
    # generate_spec 节点填:LLM 出的 ComputeSpec(dict 形式,便于序列化)
    compute_spec: dict[str, Any] | None = None
    # validate_spec 节点填:校验发现的、difflib 自动纠正不了的问题(空 = 通过)。
    # 非空 → 路由到 correct_spec 让 LLM 重做;为空 → 直接执行。
    spec_issues: list[str] = []
    # correct_spec 每修一次 +1,达到 MAX_RETRY 不再重试 → 兜底直接执行(让 execute_spec 自然报错)
    spec_retry_count: int = 0
    # execute_spec 写完后,继承字段 sql_result 装 rows,下游 chart_agent / interpret_result 原样读


class DatasetAgentContext(BaseModel):
    """新图的运行时上下文。

    大部分依赖(MySQL sessionmaker / es_client / llm)走 module-level singleton,
    Context 里只放真正按请求注入的少量字段。
    """
    user_id: str = "anonymous"

    model_config = ConfigDict(arbitrary_types_allowed=True)
