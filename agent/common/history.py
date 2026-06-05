"""会话历史落库:把一条「产出 WSStepInfo 的异步流」包装成带历史持久化的 SSE 流。

流程(服务端累加落库,不依赖前端、不信任前端数据):
  1) 确保会话存在:没传 conversation_id 就新建(title=本次问题);落库 user 消息;
  2) 首个 SSE 事件回传 {"conversation_id": N},供前端更新列表/URL;
  3) 边转发 chunk 边累加;
  4) 流结束后落库 assistant 消息(完整渲染 payload),并刷新会话 updated_at。

两个流式接口(主图 / 数据集)共用本 helper,只是 source / dataset_id 不同。
"""
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncIterator

from agent.common.reply_accumulator import ReplyAccumulator
from agent.schemas import WSStepInfo
from core.log import logger
from repositories.conversation import ConversationRepository
from services.excel_ingest import get_session_factory


def _json_default(o: Any) -> Any:
    """MySQL JSON 列写入走 json.dumps,不认识 Decimal/datetime 等。

    SQL 聚合(SUM 等)返回 Decimal、日期列返回 date/datetime,这里统一转成 JSON 安全类型,
    与前端通过 Pydantic model_dump_json 收到的形态一致(数字/ISO 字符串)。
    """
    if isinstance(o, Decimal):
        # 整数值的 Decimal 转 int,否则转 float(前端按数字渲染)
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


def _json_safe(obj: Any) -> Any:
    """递归把对象转成 JSON 可序列化(借 json round-trip,简单可靠)。"""
    return json.loads(json.dumps(obj, default=_json_default, ensure_ascii=False))


async def stream_with_history(
    chunks: AsyncIterator[WSStepInfo],
    *,
    user_id: str,
    source: str,
    query: str,
    conversation_id: int | None = None,
    dataset_id: int | None = None,
    datasource_id: str | None = None,
) -> AsyncIterator[str]:
    Session = get_session_factory()

    # 1) 确保会话存在 + 落库 user 消息
    async with Session() as session:
        repo = ConversationRepository(session)
        if conversation_id is not None:
            conv = await repo.get_owned(conversation_id, user_id)
            # 传了但不属于当前用户(或不存在)→ 不报错,新建一个,避免越权写入别人会话
            if conv is None:
                conv = await repo.create(user_id, source, title=query,
                                         dataset_id=dataset_id, datasource_id=datasource_id)
                conversation_id = conv.id
        else:
            conv = await repo.create(user_id, source, title=query,
                                     dataset_id=dataset_id, datasource_id=datasource_id)
            conversation_id = conv.id
        await repo.add_message(conversation_id, role="user", content=query)
        await session.commit()

    # 2) 首个事件:回传 conversation_id(前端据此更新会话列表/选中态)
    yield f"data: {json.dumps({'conversation_id': conversation_id})}\n\n"

    # 3) 转发 + 累加
    # 整段包 try:图执行/流式过程中任何异常都不让它冒出去把 SSE 连接冲断,
    # 而是发一张 error 卡(finish=True)优雅收尾,前端正常渲染错误、流正常结束。
    acc = ReplyAccumulator()
    try:
        async for chunk in chunks:
            acc.feed(chunk)
            yield f"data: {chunk.model_dump_json()}\n\n"
    except Exception as exc:
        logger.exception(f"流式执行异常(conversation_id={conversation_id}):{exc}")
        err_step = WSStepInfo(
            step="生成图表",
            status="error",
            data={
                "chart_type": "error",
                "title": "查询失败",
                "message": "服务处理异常,请稍后重试",
                "hint": "请稍后重试,或换一种问法",
            },
            finish=True,
        )
        acc.feed(err_step)  # 让历史落库也带上这张 error 卡
        yield f"data: {err_step.model_dump_json()}\n\n"

    # 4) 落库 assistant 消息 + 刷新会话时间
    assistant_message_id: int | None = None
    try:
        async with Session() as session:
            repo = ConversationRepository(session)
            # 落库前清洗:Decimal/datetime 等转 JSON 安全类型(MySQL JSON 列 json.dumps 不认 Decimal)
            payload = _json_safe(acc.payload())
            msg = await repo.add_message(conversation_id, role="assistant", payload=payload)
            await repo.touch(conversation_id)
            # flush 后(add_message 内已 flush)即可读自增 id;须在 commit 前取,
            # 避免 commit 后属性过期、读取触发异步懒加载报错(MissingGreenlet)。
            assistant_message_id = msg.id
            await session.commit()
    except Exception as exc:
        # 历史落库失败不应影响用户已经看到的结果,仅记日志
        logger.warning(f"会话历史落库失败(conversation_id={conversation_id}):{exc}")

    # 5) 末事件:回传 assistant 消息 id。前端「生成图表」是流结束后按需调 /chart,
    #    拿到 id 才能把 chart_config 回写到这条消息(PATCH .../messages/{id}/chart),落进历史。
    if assistant_message_id is not None:
        yield f"data: {json.dumps({'assistant_message_id': assistant_message_id})}\n\n"
