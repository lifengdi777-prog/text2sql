from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def execute_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    #实时反馈当前步骤状态
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="执行SQL语句", status="running").model_dump())