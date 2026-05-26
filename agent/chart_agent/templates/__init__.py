"""状态卡模板:metric / empty / error。

正常图表(line/bar/pie/multi_line/stacked_bar/table)由 LLM 直接产出 ECharts
option,不再需要 Python 模板。
"""
from agent.chart_agent.templates import empty, error, metric

__all__ = ["empty", "error", "metric"]
