from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def merge_recalled_infos(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="合并召回信息", status="running").model_dump())