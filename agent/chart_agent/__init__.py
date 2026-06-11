"""Chart Agent — 独立的图表生成 agent(自有 ChartAgentState / ChartAgentContext)。

入口:`from agent.chart_agent import chart_subgraph`,目前由 /chart 端点按需调用;
后续「对话内画图」意图路由也复用同一张图。

数据流(subgraph.py 里编排):
    analyze_data_shape
      ├─ render_error / render_empty / render_metric / render_table  (deterministic)
      └─ build_chart  (LLM 只选 chart_type → option_builder 用代码确定性构图)
                ↓
              END

支持 9 种 chart_type:
  - 正常图表(LLM 选型 + 代码构图): line / bar / pie / multi_line / stacked_bar / table
  - 状态卡(规则分流):              metric / empty / error
"""

def __getattr__(name: str):
    # 延迟导入:让 `import agent.chart_agent.schemas` 保持轻量,不连带拉起整张图(LLM 等依赖)
    if name == "chart_subgraph":
        from agent.chart_agent.subgraph import chart_subgraph as _sg
        return _sg
    raise AttributeError(f"module 'agent.chart_agent' has no attribute {name!r}")


__all__ = ["chart_subgraph"]
