from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def parse_query_intention(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="解析提问意图", status="running").model_dump())
    
    return  {"should_continue": True}