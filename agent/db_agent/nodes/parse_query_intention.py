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
        structured_llm = llm.with_structured_output(ParseQueryResult, method="json_mode")

        result: ParseQueryResult = await structured_llm.ainvoke([
            SystemMessage(content=prompt)
        ] + state.messages)  # type: ignore

        writer(
            WSStepInfo(
                step="解析用户意图",
                status="success",
                data={"should_continue": result.should_continue},
                guide_queries=result.guide_queries,
                #如果should_continue为True,流程继续,设置finsh为False；
                #如果should_continue为False,流程结束，设置finish为True。
                finish=not result.should_continue,
            )
        )

        print("解析用户意图结果:", result)

        return {
            "should_continue": result.should_continue,
            "guide_queries": result.guide_queries,
        }

    except Exception as e:
        writer(
            WSStepInfo(
                step="解析用户意图",
                status="error",
                ## 错误信息直接发给前端
                data={"error": str(e)},
                guide_queries=[],
                finish=True,
            )
        )
        return {"should_continue": False, "error": str(e)}