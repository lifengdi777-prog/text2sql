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


class ChartConfigBody(BaseModel):
    # 前端按需出图(或降级表格)后回写的 chart_config;None 表示清空
    chart_config: dict | None = None


class CreateBody(BaseModel):
    source: str = "db"
    dataset_id: int | None = None
    datasource_id: str | None = None
    title: str = "新对话"


class AppendMessageBody(BaseModel):
    # 归因页「保存到对话」:question 作为 user 消息,payload 作为 assistant 消息
    # (payload 形状即前端 AgentReplyMessage,历史回放直接渲染)
    question: str = ""
    payload: dict


def _conv_brief(conv) -> dict:
    return {
        "id": conv.id,
        "source": conv.source,
        "dataset_id": conv.dataset_id,
        "datasource_id": conv.datasource_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


@router.post("")
async def create_conversation(body: CreateBody, user_id: str = Depends(get_current_user)):
    """显式新建一个空白会话(用户先起名,首次提问再往里追加消息)。"""
    if body.source not in ("db", "dataset"):
        raise HTTPException(status_code=400, detail="source 只能是 db 或 dataset")
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        conv = await repo.create(
            user_id,
            source=body.source,
            title=(body.title or "新对话"),
            dataset_id=body.dataset_id,
            datasource_id=body.datasource_id,
        )
        await session.commit()
        # created_at/updated_at 是 server_default,INSERT 后未加载;
        # 在异步上下文里 refresh 读回,避免 _conv_brief 读取时触发同步延迟加载(MissingGreenlet)
        await session.refresh(conv)
        return _conv_brief(conv)


@router.get("")
async def list_conversations(
    source: str = Query("db", pattern="^(db|dataset)$"),
    dataset_id: int | None = None,
    datasource_id: str | None = None,
    user_id: str = Depends(get_current_user),
):
    """列出当前用户在某来源下的会话(每个数据源 / 每个数据集各一个列表)。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        rows = await repo.list_by_user(
            user_id, source=source, dataset_id=dataset_id, datasource_id=datasource_id
        )
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


@router.post("/{conversation_id}/messages")
async def append_message(
    conversation_id: int,
    body: AppendMessageBody,
    user_id: str = Depends(get_current_user),
):
    """往会话里追加一轮消息(user 提问 + assistant 回复),仅限归属当前用户。

    归因在独立页面进行、不落历史;用户点「保存到对话」时调这里把结论落进会话,
    重开会话可回放(payload 形状与流式落库的 assistant 消息一致)。
    """
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_owned(conversation_id, user_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
        if body.question:
            await repo.add_message(conversation_id, role="user", content=body.question)
        msg = await repo.add_message(conversation_id, role="assistant", payload=body.payload)
        await repo.touch(conversation_id)
        # flush 后(add_message 内已 flush)在 commit 前取自增 id,避免过期属性异步懒加载报错
        message_id = msg.id
        await session.commit()
    return {"ok": True, "id": message_id}


@router.patch("/{conversation_id}/messages/{message_id}/chart")
async def update_message_chart(
    conversation_id: int,
    message_id: int,
    body: ChartConfigBody,
    user_id: str = Depends(get_current_user),
):
    """前端「生成图表」后,把 chart_config 回写到对应 assistant 消息,落进历史。

    图表是流结束后按需生成的,落库时尚不存在;靠这个接口补写,重开会话即原样重现
    (含「该数据无法生成图表」对应的 table 降级——前端据 chart_type 重新呈现提示)。
    """
    Session = get_session_factory()
    async with Session() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_owned(conversation_id, user_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
        ok = await repo.update_message_chart(conversation_id, message_id, body.chart_config)
        if not ok:
            raise HTTPException(status_code=404, detail=f"消息 {message_id} 不存在")
        await session.commit()
    return {"ok": True, "id": message_id}


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
