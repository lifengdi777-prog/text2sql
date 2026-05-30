"""把一次问答的 SSE 事件流累加成「最终渲染 payload」,用于会话历史落库。

逻辑对齐前端 services/agent.ts 的 mergeReplyMessage:
  - steps:按 step 名去重,更新状态
  - result:finish 且 data 是数组 → 结果行
  - chartConfig:finish 且 data 是带 chart_type 的对象 → 图表配置(含 error/empty/metric 卡)
  - interpretation:step="数据解读" 且 data 是字符串
  - sql:执行成功事件带上的真正执行 SQL
  - guideQueries:finish 且 guide_queries 非空

产出的 payload 字段名对齐前端 AgentReplyMessage(camelCase),历史加载时可直接渲染。
"""
from typing import Any

from agent.schemas import WSStepInfo


class ReplyAccumulator:
    def __init__(self) -> None:
        self.steps: list[dict[str, str]] = []
        self.result: list[dict[str, Any]] = []
        self.chart_config: dict[str, Any] | None = None
        self.interpretation: str | None = None
        self.sql: str | None = None
        self.guide_queries: list[str] = []

    def feed(self, info: WSStepInfo) -> None:
        # 1) steps:按 step 名去重更新状态
        for s in self.steps:
            if s["step"] == info.step:
                s["status"] = info.status
                break
        else:
            # __meta__ 之类的非步骤事件不入 steps(它们没有真实 step 语义时由调用方避免传入)
            self.steps.append({"step": info.step, "status": info.status})

        data = info.data
        # 2) result / chartConfig:都靠 finish 事件区分(数组=结果行,带 chart_type 的对象=图表)
        if info.finish and isinstance(data, list):
            self.result = data
        elif info.finish and isinstance(data, dict) and isinstance(data.get("chart_type"), str):
            self.chart_config = data

        # 3) 数据解读(纯文本,不带 finish)
        if info.step == "数据解读" and isinstance(data, str):
            self.interpretation = data

        # 4) 真正执行的 SQL
        if info.sql:
            self.sql = info.sql

        # 5) 引导问题
        if info.finish and info.guide_queries:
            self.guide_queries = list(info.guide_queries)

    def payload(self) -> dict[str, Any]:
        """组装成前端 AgentReplyMessage 形状(缺 id/role,加载时补)。"""
        return {
            "steps": self.steps,
            "result": self.result,
            "chartConfig": self.chart_config,
            "interpretation": self.interpretation,
            "sql": self.sql,
            "guideQueries": self.guide_queries,
            # 落库的都是已完成的一轮(含 error 卡也算完成),统一 success
            "status": "success",
        }
