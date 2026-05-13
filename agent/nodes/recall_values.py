from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def recall_values(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="召回业务数据", status="running").model_dump())