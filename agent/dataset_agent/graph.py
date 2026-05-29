"""数据集分析 graph(独立于主 DW 图)。

数据流:
  START
    ↓
  load_schema       (从 MySQL 拉 schema_json → 渲染 markdown)
    ↓
  recall_values     (ES 用用户原 query 召回真实值)
    ↓
  generate_spec     (LLM 出 ComputeSpec JSON)
    ↓
  execute_spec      (load parquet → pandas 跑 spec → state.sql_result)
    ↓ 并行 fan-out(二者都吃 state.sql_result,各自能处理 state.error)
    ├─ generate_chart   (复用主项目 chart_subgraph,出 ECharts spec)
    └─ interpret_result (复用主项目 interpret_result,出自然语言解读)
    ↓
  END

跟主 DW 图完全隔离:
  - 自己的 state schema(继承 WSAgentState,sql_result/chart_config/interpretation 字段共用)
  - 自己的 context schema(轻量,不要 DW 的那一堆 repos)
  - 复用 chart_subgraph + interpret_result 节点(它们不读 context,duck-typing 兼容)
"""
from langgraph.graph import END, START, StateGraph

from agent.chart_agent import chart_subgraph
from agent.dataset_agent.nodes.execute_spec import execute_spec
from agent.dataset_agent.nodes.generate_spec import generate_spec
from agent.dataset_agent.nodes.load_schema import load_schema
from agent.dataset_agent.nodes.parse_intent import parse_intent
from agent.dataset_agent.nodes.recall_values import recall_values
from agent.dataset_agent.schemas import DatasetAgentContext, DatasetAgentState
from agent.nodes.interpret_result import interpret_result


def _route_after_intent(state: DatasetAgentState):
    # 闲聊 / 与数据无关 → 直接结束(parse_intent 已发 guide_queries 引导用户);
    # 正常提问(以及 load_schema 出错的兜底)→ 继续走计算管线。
    return "recall_values" if state.should_continue else END


def _build():
    g = StateGraph(state_schema=DatasetAgentState, context_schema=DatasetAgentContext)

    g.add_node("load_schema", load_schema)
    g.add_node("parse_intent", parse_intent)
    g.add_node("recall_values", recall_values)
    g.add_node("generate_spec", generate_spec)
    g.add_node("execute_spec", execute_spec)
    # 复用主项目的子图 / 节点(原样,不动)
    g.add_node("generate_chart", chart_subgraph)
    g.add_node("interpret_result", interpret_result)

    # 主路径:先加载 schema,再做(宽松的)意图识别,闲聊在此短路
    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "parse_intent")
    g.add_conditional_edges(
        "parse_intent",
        _route_after_intent,
        {"recall_values": "recall_values", END: END},
    )
    g.add_edge("recall_values", "generate_spec")
    g.add_edge("generate_spec", "execute_spec")

    # 执行后并行 fan-out:
    # · chart_subgraph 内部已处理 state.error / sql_result==None → 出 error 卡或 empty 卡
    # · interpret_result 检测到 state.error / sql_result 空 → 直接跳过
    g.add_edge("execute_spec", "generate_chart")
    g.add_edge("execute_spec", "interpret_result")
    g.add_edge("generate_chart", END)
    g.add_edge("interpret_result", END)

    return g.compile()


dataset_graph = _build()
