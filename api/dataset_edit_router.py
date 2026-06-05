"""「智能助手」编辑接口:用自然语言对数据集副本增删改 → 预览 → 保样式下载。

接口(均经 require_owned_dataset 鉴权;协议与问数一致,前端可复用渲染):
  POST   /dataset/{id}/edit/session              建/复用编辑会话 + 返回初始预览
  POST   /dataset/{id}/edit/{sid}/message  (SSE) 一轮自然语言变更
  POST   /dataset/{id}/edit/{sid}/undo           撤销最后一步 + 返回预览
  GET    /dataset/{id}/edit/{sid}/download       重放全部 → 保样式导出 xlsx
  DELETE /dataset/{id}/edit/{sid}                 丢弃会话

编辑结果"下载即终点"(决策 8):永不回写源数据集,要查改后的数据请重新上传。
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from langchain.messages import HumanMessage

from agent.dataset_edit_agent.graph import dataset_edit_graph
from agent.dataset_edit_agent.schemas import DatasetEditContext, DatasetEditState
from api.deps import get_current_user, require_owned_dataset
from core.log import logger
from repositories.dataset_edit import DatasetEditRepository
from services.dataset_loader import get_dataset_info
from services.duckdb_edit import EditWorkbook
from services.excel_ingest import get_session_factory
from services.xlsx_export import export_with_info

router = APIRouter(prefix="/dataset")


class EditMessageBody(BaseModel):
    instruction: str
    confirmed: bool = False
    active_sheet: str | None = None  # 用户当前选中的 sheet tab → 默认操作对象


# ───────────────────────── 同步辅助(走 to_thread)─────────────────────────
def _original_sheets(info: dict) -> set[str]:
    """原文件自带的 sheet 名(来自数据集 schema);其余都是编辑中新建的(汇总/宽表)。"""
    return set(((info.get("schema") or {}).get("sheets") or {}).keys())


def _preview_all(info: dict, active_ops: list[str], size: int = 20) -> list[dict]:
    """物化 + 重放 active op → 各 sheet 第 0 页预览(初始渲染用)。created 标记新建 sheet(可删)。"""
    original = _original_sheets(info)
    wb = EditWorkbook.from_dataset(info)
    try:
        wb.replay(active_ops)
        return [{**wb.preview(s, page=0, size=size), "created": s not in original}
                for s in wb.sheets()]
    finally:
        wb.close()


def _preview_one(info: dict, active_ops: list[str], sheet: str, page: int, size: int) -> dict:
    """物化 + 重放 → 指定 sheet 的某一页(翻页用)。"""
    original = _original_sheets(info)
    wb = EditWorkbook.from_dataset(info)
    try:
        wb.replay(active_ops)
        if sheet not in wb.sheets() and wb.sheets():
            sheet = wb.sheets()[0]
        return {**wb.preview(sheet, page=page, size=size), "created": sheet not in original}
    finally:
        wb.close()


def _ops_payload(ops: list) -> list[dict]:
    """把已应用的 op 列表转成给前端重建历史气泡的数据。"""
    return [
        {"seq": o.seq, "nl": o.nl, "sql": o.sql, "op_type": o.op_type,
         "target_sheet": o.target_sheet, "affected": o.affected}
        for o in ops
    ]


# ───────────────────────── 会话 ─────────────────────────
@router.post("/{dataset_id}/edit/session")
async def open_session(dataset_id: int, user_id: str = Depends(get_current_user)):
    ds = await require_owned_dataset(dataset_id, user_id)
    if ds.status != "ready":
        raise HTTPException(status_code=409, detail=f"数据集当前状态={ds.status},不可编辑")

    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        sess = await repo.get_or_create_active(dataset_id, user_id)
        sid = sess.id
        active_ops = await repo.active_sql(sid)
        ops_payload = _ops_payload(await repo.list_ops(sid, active_only=True))
        await s.commit()

    info = await get_dataset_info(dataset_id)
    sheets = await asyncio.to_thread(_preview_all, info, active_ops)
    return {"session_id": sid, "ops_count": len(active_ops), "sheets": sheets, "ops": ops_payload}


@router.delete("/{dataset_id}/edit/{session_id}")
async def discard_session(dataset_id: int, session_id: int,
                          user_id: str = Depends(get_current_user)):
    await require_owned_dataset(dataset_id, user_id)
    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        if await repo.get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")
        await repo.discard(session_id)
        await s.commit()
    return {"ok": True}


# ───────────────────────── 分页预览 ─────────────────────────
@router.get("/{dataset_id}/edit/{session_id}/preview")
async def preview_page(dataset_id: int, session_id: int, sheet: str,
                       page: int = 0, size: int = 20,
                       user_id: str = Depends(get_current_user)):
    await require_owned_dataset(dataset_id, user_id)
    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        if await repo.get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")
        active_ops = await repo.active_sql(session_id)
    info = await get_dataset_info(dataset_id)
    return await asyncio.to_thread(_preview_one, info, active_ops, sheet, page, size)


# ───────────────────────── 一轮编辑(SSE)─────────────────────────
@router.post("/{dataset_id}/edit/{session_id}/message")
async def edit_message(dataset_id: int, session_id: int, body: EditMessageBody,
                       user_id: str = Depends(get_current_user)):
    await require_owned_dataset(dataset_id, user_id)
    Session = get_session_factory()
    async with Session() as s:
        if await DatasetEditRepository(s).get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")

    state = DatasetEditState(
        messages=[HumanMessage(content=body.instruction)],
        dataset_id=dataset_id, session_id=session_id,
        active_sheet=body.active_sheet, confirmed=body.confirmed,
    )
    context = DatasetEditContext(user_id=user_id)

    async def _sse():
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"
        try:
            # subgraphs=True 让节点内 stream_writer 的 WSStepInfo 冒泡上来
            async for _ns, chunk in dataset_edit_graph.astream(
                input=state, context=context, stream_mode="custom", subgraphs=True,
            ):
                yield f"data: {chunk.model_dump_json()}\n\n"
        except Exception as exc:  # 不让异常冲断 SSE,发 error 卡优雅收尾
            logger.exception(f"编辑流异常(session={session_id}):{exc}")
            from agent.schemas import WSStepInfo
            err = WSStepInfo(step="应用变更", status="error",
                             data={"error": "服务处理异常,请重试"}, finish=True)
            yield f"data: {err.model_dump_json()}\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


# ───────────────────────── 撤销 ─────────────────────────
@router.post("/{dataset_id}/edit/{session_id}/undo")
async def undo(dataset_id: int, session_id: int,
               user_id: str = Depends(get_current_user)):
    await require_owned_dataset(dataset_id, user_id)
    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        if await repo.get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")
        undone = await repo.undo_last(session_id)
        if undone is not None:
            await repo.touch(session_id)
        active_ops = await repo.active_sql(session_id)
        ops_payload = _ops_payload(await repo.list_ops(session_id, active_only=True))
        await s.commit()

    info = await get_dataset_info(dataset_id)
    sheets = await asyncio.to_thread(_preview_all, info, active_ops)
    return {"undone": undone is not None, "ops_count": len(active_ops),
            "sheets": sheets, "ops": ops_payload}


# ───────────────────────── 删除新建的 sheet ─────────────────────────
@router.delete("/{dataset_id}/edit/{session_id}/sheet")
async def delete_sheet(dataset_id: int, session_id: int, name: str,
                       user_id: str = Depends(get_current_user)):
    """删除编辑中**新建**的 sheet(汇总/宽表):撤销所有作用于它的操作(及对应历史),原文件 sheet 不可删。

    返回结构与 undo 一致(sheets + ops),前端据此刷新 tab 与历史。
    """
    await require_owned_dataset(dataset_id, user_id)
    info = await get_dataset_info(dataset_id)
    if name in _original_sheets(info):
        raise HTTPException(status_code=400, detail="原文件的工作表不能删除,只能删除编辑中新建的表")

    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        if await repo.get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")

        count = await repo.deactivate_sheet_ops(session_id, name)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"未找到可删除的工作表「{name}」")
        await repo.touch(session_id)

        active_ops = await repo.active_sql(session_id)
        ops_payload = _ops_payload(await repo.list_ops(session_id, active_only=True))
        # 提交前试重放:若该表被其它表依赖(删后重放失败)→ 回滚,提示先删依赖方
        try:
            sheets = await asyncio.to_thread(_preview_all, info, active_ops)
        except Exception as exc:
            await s.rollback()
            logger.warning(f"删 sheet「{name}」后重放失败,已回滚(session={session_id}):{exc}")
            raise HTTPException(status_code=409, detail="该表被其它表引用,请先删除依赖它的表")
        await s.commit()

    return {"deleted": True, "removed_ops": count, "ops_count": len(active_ops),
            "sheets": sheets, "ops": ops_payload}


# ───────────────────────── 下载(保样式)─────────────────────────
@router.get("/{dataset_id}/edit/{session_id}/download")
async def download(dataset_id: int, session_id: int,
                   user_id: str = Depends(get_current_user)):
    await require_owned_dataset(dataset_id, user_id)
    Session = get_session_factory()
    async with Session() as s:
        repo = DatasetEditRepository(s)
        if await repo.get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")
        active_ops = await repo.active_sql(session_id)

    info = await get_dataset_info(dataset_id)
    filename, data = await asyncio.to_thread(export_with_info, info, active_ops)

    # 文件名含中文 → RFC 5987 编码,避免 header 非 ASCII 报错
    quoted = urllib.parse.quote(filename)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )
