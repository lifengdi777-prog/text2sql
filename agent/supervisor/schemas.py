"""Supervisor 的 State / Context schema。

State 只装路由决策需要的最小信息(消息 + 历史 + dataset_id),
子 agent 的完整 State 由包装节点按需构造,互不渗透。
"""
from typing import Annotated, Any, Literal

from langchain.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, ConfigDict


class SupervisorState(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages]
    # 多轮历史(question/sql/结果快照),入口加载后注入:
    # 路由用「上一轮问题」做指代上下文,查询子 agent 用它做多轮改写
    history: list[dict[str, Any]] | None = None
    # dataset 入口专用:构造 DatasetAgentState 时透传
    dataset_id: int | None = None
    # route_intent 的路由决策
    route: Literal["chart", "attribution", "query"] | None = None
    # route_intent 的 LLM 已完成「分流+改写+守门」(messages[-1] 已是自包含问题)→
    # 子图意图节点据此短路,不再重复调用 LLM
    intent_pre_parsed: bool = False


class SupervisorContext(BaseModel):
    """请求级注入:子查询 agent 的 context 原样透传,chart_agent 取数靠 conversation_id。"""
    # WSAgentContext(db 入口)或 DatasetAgentContext(dataset 入口),包装节点直接转交
    query_context: Any = None
    # 当前会话 id:chart_agent 的 load_rows 节点按它从会话历史取数
    conversation_id: int | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
