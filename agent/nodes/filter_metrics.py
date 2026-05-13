from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def filter_metrics(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    #实时反馈当前步骤状态
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="过滤指标信息", status="running").model_dump())