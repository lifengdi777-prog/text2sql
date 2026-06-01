"""Langfuse 可观测客户端封装(v3)。

设计原则:零侵入。
  - enabled=false / 缺 key / SDK 不可用 → get_langfuse_handler() 返回 None,
    build_run_config() 也不会塞 callbacks,整条链路当 Langfuse 不存在。
  - 配置了就初始化一次全局 Langfuse client,复用一个 CallbackHandler;
    在 graph.astream(config=build_run_config(...)) 注入即可把整条图(每节点/每次 LLM 调用)
    的 trace 发到 Langfuse。

用法:
    cfg = build_run_config("db_query", user_id=uid, request_id=rid, session_id=conv_id, query=q)
    async for ... in graph.astream(input=..., context=..., config=cfg, ...): ...
"""
from __future__ import annotations

from typing import Any

from conf.app_config import app_config
from core.log import logger

_handler: Any = None
_inited: bool = False


def get_langfuse_handler() -> Any | None:
    """返回复用的 Langfuse CallbackHandler;未启用/初始化失败 → None(只初始化一次)。"""
    global _handler, _inited
    if _inited:
        return _handler
    _inited = True

    cfg = getattr(app_config, "langfuse", None)
    if cfg is None or not cfg.enabled or not cfg.public_key or not cfg.secret_key:
        logger.info("Langfuse 未启用(enabled=false 或缺 key),跳过追踪")
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        # 初始化全局 client(v3 走 OpenTelemetry,init 不会阻塞/联网探活)
        Langfuse(public_key=cfg.public_key, secret_key=cfg.secret_key, host=cfg.host)
        _handler = CallbackHandler()
        logger.info(f"Langfuse 追踪已启用 → {cfg.host}")
    except Exception as exc:
        # 初始化失败绝不影响主流程,降级为"不追踪"
        logger.warning(f"Langfuse 初始化失败,禁用追踪:{exc}")
        _handler = None
    return _handler


def build_run_config(
    run_name: str,
    *,
    user_id: str | None = None,
    user_name: str | None = None,
    session_id: Any | None = None,
    request_id: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """组装传给 graph.astream 的 config:Langfuse callbacks(若启用) + 运行名 + 元数据。

    未启用 Langfuse 时只带 run_name/metadata(对 LangGraph 无害),不带 callbacks。
    metadata 里的 langfuse_user_id / langfuse_session_id 会被 Langfuse 用来按用户/会话归类 trace。
    """
    config: dict[str, Any] = {}

    handler = get_langfuse_handler()
    if handler is not None:
        config["callbacks"] = [handler]

    if run_name:
        config["run_name"] = run_name

    metadata: dict[str, Any] = {}
    if request_id:
        metadata["request_id"] = request_id
    if query:
        metadata["query"] = query[:200] if isinstance(query, str) else query
    # Langfuse 的 User 维度(Users 面板按它归类):以数字 id 为主(稳定归类),
    # 若能拿到用户名则拼上,显示成 "1 (admin)" —— 既能按 id 归类又可读。
    # 老 token 拿不到用户名 → 只显示 id;连 id 都没有 → 退回用户名。
    uid = str(user_id) if user_id else None
    if uid and user_name:
        metadata["langfuse_user_id"] = f"{uid} ({user_name})"
    elif uid:
        metadata["langfuse_user_id"] = uid
    elif user_name:
        metadata["langfuse_user_id"] = user_name
    # 同时把数字 id 与用户名分别留在 metadata,便于检索/精确定位。
    if uid:
        metadata["user_id"] = uid
    if user_name:
        metadata["user_name"] = user_name
    if session_id is not None:
        metadata["langfuse_session_id"] = str(session_id)
    if metadata:
        config["metadata"] = metadata

    return config
