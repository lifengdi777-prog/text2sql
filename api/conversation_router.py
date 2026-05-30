"""会话历史接口:列表 / 详情 / 改名 / 删除。

全部走 get_current_user,并按 user_id 做归属校验 —— 用户只能看/改/删自己的会话。
  GET    /conversations?source=db|dataset&dataset_id=   列出我的会话(最近优先)
  GET    /conversations/{id}                            会话 + 全部消息
  PATCH  /conversations/{id}                            改名
  DELETE /conversations/{id}                            删会话及其消息
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_current_user
from repositories.conversation import ConversationRepository
from services.excel_ingest import get_session_factory

router = APIRouter(prefix="/conversations", tags=["conversations"])


class RenameBody(BaseModel):
    title: str


def _conv_brief(conv) -> dict:
    return {
        "id": conv.id,
        "source": conv.source,
        "dataset_id": conv.dataset_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


@router.get("")
async def list_conversations(
    source: str = Query("db", pattern="^(db|dataset)$"),
    dataset_id: int | None = None,
    user_id: str = Depends(get_current_user),
):
    """列出当前用户在某来源下的会话(主图全局一个列表 / 每个数据集各一个列表)。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        rows = await repo.list_by_user(user_id, source=source, dataset_id=dataset_id)
    return [_conv_brief(c) for c in rows]


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: int, user_id: str = Depends(get_current_user)):
    """取会话及其全部消息(仅限归属当前用户)。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_owned(conversation_id, user_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
        msgs = await repo.list_messages(conversation_id)
    return {
        **_conv_brief(conv),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "payload": m.payload,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    }


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: int,
    body: RenameBody,
    user_id: str = Depends(get_current_user),
):
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_owned(conversation_id, user_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
        await repo.rename(conversation_id, body.title)
        await session.commit()
    return {"ok": True, "id": conversation_id, "title": body.title[:255]}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: int, user_id: str = Depends(get_current_user)):
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_owned(conversation_id, user_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
        await repo.delete(conversation_id)
        await session.commit()
    return {"ok": True, "id": conversation_id}
