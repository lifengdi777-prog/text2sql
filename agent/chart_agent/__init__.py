"""Chart Agent — 图表生成子图。

主图把它当成一个普通节点接入:`from agent.chart_agent import chart_subgraph`。

数据流(subgraph.py 里编排):
    analyze_data_shape
      ├─ render_error / render_empty / render_metric   (deterministic)
      └─ generate_spec → validate_spec ⇄ correct_spec  (LLM 直出 + 校验循环)
                            ↓
                       emit_chart_config / fallback_table → END

支持 9 种 chart_type:
  - 正常图表(LLM 出 ECharts option): line / bar / pie / multi_line / stacked_bar / table
  - 状态卡(规则分流):                metric / empty / error
"""

def __getattr__(name: str):
    # 延迟导入,避免 agent.schemas 引用本包 schemas 时的循环 import
    if name == "chart_subgraph":
        from agent.chart_agent.subgraph import chart_subgraph as _sg
        return _sg
    raise AttributeError(f"module 'agent.chart_agent' has no attribute {name!r}")


__all__ = ["chart_subgraph"]
