from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from langgraph.runtime import Runtime
from pydantic import BaseModel
from agent.llm import llm
from agent.prompts import load_prompt
from langchain.messages import SystemMessage


class ParseQueryResult(BaseModel):
    should_continue: bool
    guide_queries: list[str]


async def parse_query_intention(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="解析用户意图", status="running"))
    try:
        prompt = await load_prompt("parse_query_intention")
        #作用：让 LLM 输出结构化数据，而不是普通字符串
        structured_llm = llm.with_structured_output(ParseQueryResult, method="json_mode")
        #强制 LLM 以 JSON 格式返回，再自动解析成ParseQueryResult模型实例
        result: ParseQueryResult = await structured_llm.ainvoke([
            #系统提示词
            SystemMessage(content=prompt)
            #state.messages 是之前的对话历史，包含用户和AI助手的消息，这里把它们也传给LLM，让它根据上下文来解析用户意图
        ] + state.messages) #type: ignore
        writer(WSStepInfo(step="解析用户意图", status="success"))
        print("解析用户意图结果:", result)
        return {"should_continue": result.should_continue, "guide_queries": result.guide_queries}
    except Exception as e:
        writer(WSStepInfo(step="解析用户意图", status="error"))
        return {"should_continue": False, "error": str(e)}        