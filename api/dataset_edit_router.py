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

from agent.dataset_edit_agent.runner import run_edit_message
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


# ───────────────────────── 同步辅助(走 to_thread)─────────────────────────
def _preview_all(info: dict, active_ops: list[str], limit: int = 100) -> list[dict]:
    """物化 + 重放 active op → 各 sheet 当前预览。"""
    wb = EditWorkbook.from_dataset(info)
    try:
        wb.replay(active_ops)
        return [{"sheet": s, **wb.preview(s, limit)} for s in wb.sheets()]
    finally:
        wb.close()


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
        await s.commit()

    info = await get_dataset_info(dataset_id)
    sheets = await asyncio.to_thread(_preview_all, info, active_ops)
    return {"session_id": sid, "ops_count": len(active_ops), "sheets": sheets}


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


# ───────────────────────── 一轮编辑(SSE)─────────────────────────
@router.post("/{dataset_id}/edit/{session_id}/message")
async def edit_message(dataset_id: int, session_id: int, body: EditMessageBody,
                       user_id: str = Depends(get_current_user)):
    await require_owned_dataset(dataset_id, user_id)
    Session = get_session_factory()
    async with Session() as s:
        if await DatasetEditRepository(s).get_owned(session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="编辑会话不存在")

    async def _sse():
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"
        try:
            async for step in run_edit_message(dataset_id, session_id,
                                               body.instruction, body.confirmed):
                yield f"data: {step.model_dump_json()}\n\n"
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
        await s.commit()

    info = await get_dataset_info(dataset_id)
    sheets = await asyncio.to_thread(_preview_all, info, active_ops)
    return {"undone": undone is not None, "ops_count": len(active_ops), "sheets": sheets}


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
