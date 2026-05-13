from pydantic import BaseModel
from langgraph.graph import MessagesState, add_messages
from typing import Annotated
from langchain.messages import AnyMessage
from typing import Optional, Literal


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
    #记录错误信息
    error: Optional[str] = None


#
class WSAgentContext(BaseModel):    
    pass


#实现到了每个步骤都要返回信息给前端
class WSStepInfo(BaseModel):
    #步骤的名字
    step: str
    #步骤的状态
    status: Literal['running', 'success', 'error']
    #整个的图，agent的状态
    finish: bool = False