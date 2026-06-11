"""按用户限流:保护吃 LLM 配额的端点(SSE 问数/数据集问答/智能编辑 + 按需图表)。

两道闸,均按 user_id 计:
  - 并发上限:同时进行中的 LLM 管线数。SSE 流从 acquire 到流结束(含客户端断开/
    异常)都算占用 —— 一条问数管线要串好几次 LLM 调用,长达几十秒,并发是大头;
  - 频率上限:60s 滑动窗口内的请求次数,防快速连点/脚本刷。

超限返回 429(detail 为中文提示,前端按普通错误展示)。

单进程内存实现,零依赖:所有计数操作都跑在事件循环单线程里,无需加锁。
多 worker 部署时各进程独立计数(实际上限 ≈ 配置 × worker 数);
需要跨进程精确限流时换 Redis 实现,调用方接口不变。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import HTTPException

from conf.app_config import app_config


class UserRateLimiter:
    def __init__(self, max_concurrent: int, max_per_minute: int, enabled: bool = True):
        self.enabled = enabled
        self.max_concurrent = max_concurrent
        self.max_per_minute = max_per_minute
        # user_id → 进行中的管线数 / 最近 60s 的请求时间戳
        self._running: dict[str, int] = defaultdict(int)
        self._recent: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, user_id: str) -> None:
        """进入一条 LLM 管线前调用:超限抛 429;通过则记一次请求并占一个并发槽。"""
        if not self.enabled:
            return
        now = time.monotonic()
        window = self._recent[user_id]
        # 惰性清理滑动窗口(只在该用户再次请求时清,无后台任务)
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self.max_per_minute:
            raise HTTPException(
                status_code=429,
                detail=f"请求太频繁(每分钟上限 {self.max_per_minute} 次),请稍后再试",
            )
        if self._running[user_id] >= self.max_concurrent:
            raise HTTPException(
                status_code=429,
                detail=f"并发查询过多(同时上限 {self.max_concurrent} 条),请等当前查询完成",
            )
        window.append(now)
        self._running[user_id] += 1

    def release(self, user_id: str) -> None:
        """一条管线结束时调用(stream()/slot() 会自动调,不要手动重复释放)。"""
        if not self.enabled:
            return
        n = self._running.get(user_id, 0) - 1
        if n > 0:
            self._running[user_id] = n
        else:
            # 清掉空条目,计数字典不随历史用户数膨胀
            self._running.pop(user_id, None)

    async def stream(self, user_id: str, inner: AsyncIterator[str]) -> AsyncIterator[str]:
        """包 SSE 流:调用方先 acquire 再用本方法包流,流正常结束/异常/客户端断开
        (GeneratorExit)都会释放并发槽。"""
        try:
            async for chunk in inner:
                yield chunk
        finally:
            self.release(user_id)

    @asynccontextmanager
    async def slot(self, user_id: str):
        """非流式端点(如 /chart)用:with 块内占一个槽,超限同样抛 429。"""
        self.acquire(user_id)
        try:
            yield
        finally:
            self.release(user_id)


_cfg = app_config.rate_limit
# 全局单例:所有 LLM 端点共享同一套按用户配额(保护的是同一个 LLM 账号的额度)
llm_rate_limiter = UserRateLimiter(
    max_concurrent=_cfg.max_concurrent,
    max_per_minute=_cfg.max_per_minute,
    enabled=_cfg.enabled,
)
