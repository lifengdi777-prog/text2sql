"""
Chart Agent — 图表生成子图。

主图把它当成一个普通节点接入(LangGraph 1.x subgraph 模式)。
入口:`from agent.chart_agent import chart_subgraph`

架构(三明治):
  Layer 1: analyzer.py    — 纯 Python 算数据形状(列类型/cardinality/pattern)
  Layer 2: decider.py     — Agent 决策(选图表类型 + 字段映射)
  Layer 3: renderer.py    — Python 模板渲染 ECharts JSON

支持 9 种 chart_type,前端统一按 chart_type 分发:
  正常图表(LLM 决策): line / bar / table / pie / multi_line / stacked_bar
  状态卡(规则分流):    metric / empty / error
"""
# 用 __getattr__ 做延迟导入,避免 agent.schemas 引用 chart_agent.schemas 时
# 触发 subgraph → decider → agent.schemas 的循环 import。
# 用法:`from agent.chart_agent import chart_subgraph` 仍然正常工作。

def __getattr__(name: str):
    if name == "chart_subgraph":
        from agent.chart_agent.subgraph import chart_subgraph as _sg
        return _sg
    raise AttributeError(f"module 'agent.chart_agent' has no attribute {name!r}")


__all__ = ["chart_subgraph"]
