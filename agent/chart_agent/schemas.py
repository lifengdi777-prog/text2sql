"""Chart Agent 内部数据结构。

- ChartAgentState / ChartAgentContext:本 agent 自己的运行时 State / Context
  (独立于主图的 WSAgentState,字段名保持一致,节点 duck-typing 零改动)。
- ChartTypeDecision:LLM 唯一产出(只选 chart_type)。
- DataShape / ColumnFeature:analyzer 对结果集做的形状摘要,供选型与构图用。

ECharts option 不再由 LLM 产出,改由 option_builder 用代码确定性构造。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from langchain.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel


ChartType = Literal[
    "line", "bar", "pie", "multi_line", "stacked_bar", "table",
    "metric", "empty", "error",
]

SemanticType = Literal["temporal", "categorical", "numeric"]


class ChartTypeDecision(BaseModel):
    """LLM 的职责:选 chart_type + 指出字段映射(哪列当横轴/分组/数值)。

    LLM 只做"判断"(选型 + 映射),不碰数据本身——
    透视长表、排序、填 series.data 等体力活由 option_builder 用代码做。
    字段映射的值必须是数据列名;LLM 给空或给了不存在的列名时,上层会用规则映射兜底。
    """
    chart_type: ChartType
    x_field: str | None = None              # 横轴 / 分类维度列
    value_field: str | None = None          # 主数值列
    series_field: str | None = None         # 多系列分组列(multi_line / stacked_bar 用)
    value_fields: list[str] | None = None    # 多指标分组柱:同量纲的多个数值列
    # 这份数据还适合切换成哪些图表类型(供前端手动切换按钮用);应是支持类型的子集,含当前 chart_type
    compatible_types: list[str] = []
    reason: str = ""


class ColumnFeature(BaseModel):
    name: str
    dtype: str
    semantic_type: SemanticType
    cardinality: int
    sample: list[Any]
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    # 数值列各行求和:用于判断饼图是否成立(真·占比会≈100,逐组比率不会)
    sum_value: Optional[float] = None


class DataShape(BaseModel):
    row_count: int
    columns: list[ColumnFeature]


class ChartAgentState(BaseModel):
    """Chart Agent 的运行时状态(不再寄生 WSAgentState)。

    字段名与原 WSAgentState 完全一致,6 个节点函数 duck-typing 原样可用。
    /chart 端点入口:sql_result 由前端回传结果行直接填充。
    """
    messages: Annotated[list[AnyMessage], add_messages]
    # 待绘图的结果行(出错场景为 None → render_error 卡)
    sql_result: list[dict[str, Any]] | None = None
    # 上游执行失败的错误信息(render_error 卡展示)
    error: str | None = None
    # 出错时一并展示的原 SQL(render_error 卡用)
    sql: str | None = None
    # analyzer 算出的数据形状摘要
    data_shape: DataShape | None = None
    # 最终 ECharts 配置(前端拿这个 setOption)
    chart_config: dict[str, Any] | None = None


class ChartAgentContext(BaseModel):
    """Chart Agent 的运行时上下文。

    「对话内画图」入口注入 conversation_id,load_rows 节点据此从会话历史取数;
    /chart 端点入口 rows 由前端回传,不需要取数,context 可传 None。
    """
    conversation_id: int | None = None
