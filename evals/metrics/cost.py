"""
Cost & Latency tracker。

简单版:只记端到端耗时和节点级耗时(从 stream_writer 推送的 WSStepInfo 推算)。
高级版:接 Langfuse / LangSmith 拿到每次 LLM 调用的 token 数。

这里先做简单版,留好钩子。
"""
from __future__ import annotations

import time
from typing import Any


class CostTracker:
    """单条 case 的耗时/token 收集器。"""

    def __init__(self):
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.step_timings: dict[str, dict[str, float]] = {}  # step_name -> {start, end, duration}
        self.token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self._step_starts: dict[str, float] = {}

    def start(self) -> None:
        self.start_time = time.perf_counter()

    def stop(self) -> None:
        self.end_time = time.perf_counter()

    def on_step(self, step: str, status: str) -> None:
        """根据 WSStepInfo 的 running/success/error 推算节点耗时。"""
        now = time.perf_counter()
        if status == "running":
            self._step_starts[step] = now
        elif status in ("success", "error"):
            start = self._step_starts.pop(step, None)
            if start is not None:
                self.step_timings[step] = {
                    "duration_ms": round((now - start) * 1000, 2),
                }

    def add_tokens(self, prompt: int = 0, completion: int = 0) -> None:
        self.token_usage["prompt"] += prompt
        self.token_usage["completion"] += completion
        self.token_usage["total"] = self.token_usage["prompt"] + self.token_usage["completion"]

    def to_dict(self) -> dict[str, Any]:
        total_ms = None
        if self.start_time is not None and self.end_time is not None:
            total_ms = round((self.end_time - self.start_time) * 1000, 2)
        return {
            "total_ms": total_ms,
            "step_timings": self.step_timings,
            "token_usage": dict(self.token_usage),
        }


def aggregate_latency(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总一批 case 的延迟数据,返回 p50/p95/avg。"""
    durations = [
        r["cost"]["total_ms"] for r in results
        if r.get("cost") and r["cost"].get("total_ms") is not None
    ]
    if not durations:
        return {"p50": None, "p95": None, "avg": None, "count": 0}

    durations.sort()
    n = len(durations)
    p50_idx = int(n * 0.5)
    p95_idx = min(int(n * 0.95), n - 1)
    return {
        "p50": durations[p50_idx],
        "p95": durations[p95_idx],
        "avg": round(sum(durations) / n, 2),
        "count": n,
    }
