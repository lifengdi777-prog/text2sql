"""状态卡模板:metric / empty / error。

正常图表(line/bar/pie/multi_line/stacked_bar/table)的 ECharts option 由
option_builder 用代码构造(LLM 只负责选型 + 字段映射),不走这里的模板。
"""
from agent.chart_agent.templates import empty, error, metric

__all__ = ["empty", "error", "metric"]
