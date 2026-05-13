from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from langgraph.runtime import Runtime

async def generate_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer =  runtime.stream_writer
    writer(WSStepInfo(step="生成SQL语句", status="running").model_dump())