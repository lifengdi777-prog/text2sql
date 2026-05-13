from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def recall_columns(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="召回字段信息", status="running").model_dump())