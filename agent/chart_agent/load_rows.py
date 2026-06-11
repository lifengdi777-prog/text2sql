"""取数节点:对话内画图时,从会话历史里拿「最近一次查询」的结果行。

两个入口共用同一张图,行为差异全在本节点:
  - /chart 端点:前端已回传 rows(sql_result 非 None)→ 直接放行,零开销;
  - 对话内画图(supervisor 路由):state 里没有数据,按 context.conversation_id
    从会话历史找最近一条带非空结果的 assistant 消息,把 rows/sql 填进 state。
    找不到(新会话首句就要画图 / 历史里全是失败轮)→ 发说明卡,流程结束。
"""
from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from agent.chart_agent.schemas import ChartAgentContext, ChartAgentState
from agent.schemas import WSStepInfo
from core.log import logger


async def _load_last_result(conversation_id: int) -> dict[str, Any] | None:
    """开一个短会话查最近带结果的一轮,用完即还连接。独立成函数,便于测试替换。"""
    from repositories.conversation import ConversationRepository
    from services.excel_ingest import get_session_factory

    async with get_session_factory()() as session:
        return await ConversationRepository(session).load_last_result(conversation_id)


async def load_rows(state: ChartAgentState, runtime: Runtime[ChartAgentContext]):
    # rows 已就位(/chart 入口,空数组也算有值 → 走 render_empty)或上游已带错误
    # (→ 走 render_error)→ 本节点不做任何事,直接放行给 analyze_data_shape。
    if state.sql_result is not None or state.error:
        return {}

    writer = runtime.stream_writer
    ctx = runtime.context
    conversation_id = ctx.conversation_id if ctx is not None else None
    writer(WSStepInfo(step="读取查询结果", status="running"))

    last: dict[str, Any] | None = None
    if conversation_id is not None:
        try:
            last = await _load_last_result(conversation_id)
        except Exception as exc:
            # 取数失败不抛出:按"无数据"走说明卡,不让一次历史读库失败冲断 SSE 流
            logger.exception(f"读取会话历史结果失败(conversation_id={conversation_id}):{exc}")

    if not last:
        # 没有可画的数据 → 发一张 empty 风格说明卡收尾(data 带 chart_type,
        # 前端按 chartConfig 渲染,落库由 ReplyAccumulator 同样捕获)。
        # 不更新 state,路由函数看 sql_result 仍为 None → END。
        writer(WSStepInfo(
            step="读取查询结果",
            status="success",
            data={
                "chart_type": "empty",
                "title": "暂无可生成图表的数据",
                "message": "本会话还没有可用的查询结果",
                "hint": "请先提问查询数据,拿到结果后再让我生成图表",
            },
            finish=True,
        ))
        return {}

    # 把取到的结果行原样发给前端(数组 + finish,与 execute_sql 的结果事件同协议):
    # 前端 ChartPanel 的「切换图型 / 表格」按 message.result 在本地重建,本轮消息必须
    # 自带 rows —— 否则只有数据内嵌在 option 里的默认图能显示,一切换就空白。
    # 落库侧 ReplyAccumulator 同样按结果行捕获进 payload.result,历史回放可正常切换。
    writer(WSStepInfo(
        step="读取查询结果",
        status="success",
        data=last["rows"],
        sql=last.get("sql"),
        finish=True,
    ))
    # sql 一并带回:后续构图出错降级 error 卡时能展示来源 SQL
    return {"sql_result": last["rows"], "sql": last.get("sql")}
