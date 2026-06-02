import json

from agent.schemas import WSAgentContext, WSAgentState, WSStepInfo
from langgraph.runtime import Runtime
from pydantic import BaseModel
from agent.llm import llm
from agent.prompts import load_prompt
from langchain.messages import SystemMessage, HumanMessage


class ParseQueryResult(BaseModel):
    # 多轮:把当前追问结合历史改写成的自包含问题(下游只认这句)。
    # 首轮或新话题时,它就等于原问题本身。
    standalone_query: str
    should_continue: bool
    guide_queries: list[str]


def _render_history(history: list[dict] | None) -> str:
    """把最近几轮历史渲染成可读文本,供 LLM 做指代消解。

    每轮给三样:用户问题 + 该轮 SQL(换参数型靠它换槽位)+ 结果前 N 行快照(位置型/名称型靠它取值)。
    """
    if not history:
        return "（无历史对话，这是用户的第一轮提问）"
    blocks: list[str] = []
    for idx, turn in enumerate(history, 1):
        seg = [f"## 第 {idx} 轮", f"用户问题：{turn.get('question', '')}"]
        if turn.get("sql"):
            seg.append(f"该轮 SQL：{turn['sql']}")
        rows = turn.get("rows") or []
        if rows:
            seg.append(f"结果快照(前 {len(rows)} 行)：{json.dumps(rows, ensure_ascii=False, default=str)}")
        else:
            seg.append("结果快照：（无）")
        blocks.append("\n".join(seg))
    return "\n\n".join(blocks)


async def parse_query_intention(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="解析用户意图", status="running"))

    try:
        prompt = await load_prompt("parse_query_intention")
        structured_llm = llm.with_structured_output(ParseQueryResult, method="json_mode")

        # 历史单独作为一条 SystemMessage 注入(不塞进 messages),这样提示词模板里那一堆
        # JSON 大括号无需转义;当前追问仍是 state.messages 的最后一条(HumanMessage)。
        history_msg = SystemMessage(
            content="# 对话历史（最近几轮，供多轮改写指代消解用）\n" + _render_history(state.history)
        )

        result: ParseQueryResult = await structured_llm.ainvoke([
            SystemMessage(content=prompt),
            history_msg,
        ] + state.messages)  # type: ignore

        writer(
            WSStepInfo(
                step="解析用户意图",
                status="success",
                data={
                    "should_continue": result.should_continue,
                    "standalone_query": result.standalone_query,
                },
                guide_queries=result.guide_queries,
                #如果should_continue为True,流程继续,设置finsh为False；
                #如果should_continue为False,流程结束，设置finish为True。
                finish=not result.should_continue,
            )
        )

        print("解析用户意图结果:", result)

        update: dict = {
            "should_continue": result.should_continue,
            "guide_queries": result.guide_queries,
        }
        # 改写成功 → 用自包含问题「原地替换」当前这条消息(复用同一个 id,add_messages 见到
        # 相同 id 会替换而非追加)。这样 messages 长度不变,messages[0] 与 messages[-1] 都是
        # 改写后的完整问题:读 [-1] 的召回/SQL 节点、读 [0] 的解读/图表标题/列名翻译节点,全部拿到
        # 同一句完整问题,下游零改动,且图表标题/解读不再出现“那华北呢”这种残句。
        if result.should_continue and result.standalone_query:
            orig = state.messages[-1]
            update["messages"] = [HumanMessage(content=result.standalone_query, id=orig.id)]
        return update

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
