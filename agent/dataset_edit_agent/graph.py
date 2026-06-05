"""「智能助手」编辑 graph(独立子图,与问数图同套路)。

数据流:
  START
    ↓
  parse_intent     (edit/query/chitchat 三分流;query/chitchat 发引导后直接 END)
    ↓ edit
  generate_sql     (取 op + 物化快照 + LLM 出 DML/DDL)
    ↓
  validate_sql     (静态校验 + 试执行绑定校验 + 算 diff)
    ├─ 通过           → apply_edit
    ├─ 有问题且可重试  → correct_sql → validate_sql(封顶 MAX_RETRY)
    └─ 查询/重试用尽   → END(已发引导/错误卡)
    ↓
  apply_edit       (破坏性未确认 → 待确认卡;否则落 op + 发预览/diff)
    ↓
  END

为什么用图:校验⇄修正是个带上限的环,用条件边表达最自然;且和问数图风格统一。
线性其余部分照常。
"""
from langgraph.graph import END, START, StateGraph

from agent.dataset_edit_agent.nodes.apply_edit import apply_edit
from agent.dataset_edit_agent.nodes.correct_sql import correct_sql
from agent.dataset_edit_agent.nodes.generate_sql import generate_sql
from agent.dataset_edit_agent.nodes.parse_intent import parse_intent
from agent.dataset_edit_agent.nodes.validate_sql import validate_sql
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState


def _route_after_intent(state: DatasetEditState):
    # 编辑 → 继续生成;闲聊/查询/加载失败 → 结束(parse_intent 已发引导/错误卡)
    return "generate_sql" if state.should_continue else END


def _route_after_validate(state: DatasetEditState):
    # 已发终态卡(查询引导 / 重试用尽错误)→ 结束
    if state.terminal:
        return END
    # 有问题(且未到上限,validate 未置 terminal)→ 让 LLM 修正
    if state.sql_issues:
        return "correct_sql"
    # 通过 → 应用
    return "apply_edit"


def _build():
    g = StateGraph(state_schema=DatasetEditState, context_schema=DatasetEditContext)
    g.add_node("parse_intent", parse_intent)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_sql", validate_sql)
    g.add_node("correct_sql", correct_sql)
    g.add_node("apply_edit", apply_edit)

    g.add_edge(START, "parse_intent")
    g.add_conditional_edges("parse_intent", _route_after_intent,
                            {"generate_sql": "generate_sql", END: END})
    g.add_edge("generate_sql", "validate_sql")
    g.add_conditional_edges("validate_sql", _route_after_validate,
                            {"correct_sql": "correct_sql", "apply_edit": "apply_edit", END: END})
    g.add_edge("correct_sql", "validate_sql")
    g.add_edge("apply_edit", END)
    return g.compile()


dataset_edit_graph = _build()
