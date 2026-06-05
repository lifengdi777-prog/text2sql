"""编辑子图节点共用小工具。"""
from langchain.messages import HumanMessage


def latest_user_query(messages) -> str:
    """取最近一条用户提问文本(兼容单轮/多轮,从尾部找最后一条 HumanMessage)。"""
    for m in reversed(messages or []):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""
