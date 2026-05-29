"""dataset_agent 节点共用的小工具。"""
from langchain.messages import HumanMessage


def latest_user_query(messages) -> str:
    """取最近一条用户提问的文本。

    多轮对话时 messages 会累积成 [Human, AI, Human, ...],直接取 messages[0]
    永远拿到第一轮的问题。这里从尾部找最后一条 HumanMessage,兼容单轮与多轮。
    """
    for m in reversed(messages or []):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""
