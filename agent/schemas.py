from pydantic import BaseModel, ConfigDict
from langgraph.graph import MessagesState, add_messages
from typing import Annotated
from langchain.messages import AnyMessage
from typing import Optional, Literal
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from repositories.es import ESRepository
from repositories.mysql import MetaDBRepository, DWDBRepository
from dtos.meta import ColumnInfo, MetricInfo, ValueInfo

class WSAgentState(BaseModel):
    #Annotated的作用是为messages字段添加一个额外的验证器add_messages，
    # 这个验证器会在messages字段被赋值时被调用。
    #add_messages是langgraph的方法，用于将新的消息添加到现有的消息列表中。
    messages: Annotated[list[AnyMessage], add_messages]
    #Optional的作用是表示这个字段(should_continue)是可选的，
    # 可以为bool类型的值，也可以为None。
    should_continue: Optional[bool] = None
    #如果should_continue为false，
    # 给出用户指导性的查询语句(guide_queries)，帮助用户继续进行交互。
    guide_queries: Optional[list[str]] = None
    #关键词列表，存储从用户查询中提取的关键词，供后续节点使用
    keywords: list[str] | None = None
    #记录召回的列信息
    recalled_columns: list[ColumnInfo] | None = None
    #记录召回的指标信息
    recalled_metrics: list[MetricInfo] | None = None
    #记录召回的数值信息
    value_infos: list[ValueInfo] | None = None
    #记录错误信息
    error: Optional[str] = None


#WSAgentContext是一个Pydantic模型，定义了在整个图的执行过程中共享的上下文信息。
class WSAgentContext(BaseModel):    
    column_qdrant_repo: ColumnQdrantRepository
    metric_qdrant_repo: MetricQdrantRepository
    es_repo: ESRepository
    meta_db_repo: MetaDBRepository
    dw_db_repo: DWDBRepository
    #全是自定义类，所以需要在模型配置中设置arbitrary_types_allowed=True，
    #允许使用任意类型的字段。
    model_config = ConfigDict(arbitrary_types_allowed=True)


#实现到了每个步骤都要返回信息给前端
class WSStepInfo(BaseModel):
    #步骤的名字
    step: str
    #步骤的状态
    status: Literal['running', 'success', 'error']
    #整个的图，agent的状态
    finish: bool = False