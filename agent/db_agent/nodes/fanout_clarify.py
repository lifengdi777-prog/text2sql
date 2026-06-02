from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from core.log import logger


# 检测到扇出风险时给用户的"安全改法"(模板):
#   · 把维度换成可唯一归因的(产品/工厂等)→ 不扇出;
#   · 去重计数口径 / 关系类查询 → 天然不受扇出影响。
FANOUT_GUIDE_QUERIES = [
    "统计各产品的实际产量",
    "统计各工厂的实际产量",
    "统计各供应商供货的产品种类数",
    "查询某供应商供货了哪些产品",
]

# 给用户看的扇出风险说明(前端会配警告图标 + 危险色渲染)。
FANOUT_MESSAGE = (
    "检测到『扇出风险』：你统计的指标与所选维度是多对多关系，直接汇总会因数据被重复连接而"
    "重复计算（各组结果彼此重叠、不可相加）。请改用下面更明确的口径重新提问："
)


async def fanout_clarify(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    """检测到扇出风险 → 本轮不生成 SQL,改为像"意图识别拒绝"那样发 guide_queries 并结束本轮,
    让用户重新明确口径。用户下一次提问是一次全新运行,会自动重新经过 parse_query_intention。

    输出形态与 parse_query_intention 的拒绝完全一致(should_continue=False + guide_queries +
    finish=True),前端无需区分、无需改动。
    """
    writer = runtime.stream_writer
    writer(WSStepInfo(step="扇出风险确认", status="running"))

    logger.info(f"检测到扇出风险,发引导问法并结束本轮。warning={state.fanout_warning}")
    writer(WSStepInfo(
        step="扇出风险确认",
        status="success",
        # fanout=True 让前端用"警告图标 + 危险色"渲染,与普通意图引导区分;message 是给用户的风险说明。
        data={"should_continue": False, "fanout": True, "message": FANOUT_MESSAGE},
        guide_queries=FANOUT_GUIDE_QUERIES,
        finish=True,
    ))
    return {"should_continue": False, "guide_queries": FANOUT_GUIDE_QUERIES}
