from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def extract_keywords(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    #实时反馈当前步骤状态
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="提取关键词", status="running").model_dump())