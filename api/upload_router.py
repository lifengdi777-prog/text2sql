"""数据集相关接口:上传 / 列表 / 详情 / 删除。

身份统一走 api.deps.get_current_user(过渡期 = X-Client-Id 头);
所有按 dataset_id 的操作先经 require_owned_dataset 校验归属,杜绝越权访问。
"""
import asyncio

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from api.deps import get_current_user, require_owned_dataset
from core.log import logger
from repositories.upload import UploadDatasetRepository
from services.excel_ingest import (
    delete_dataset,
    get_session_factory,
    ingest_excel,
    reprocess_with_headers,
)

router = APIRouter(prefix="/dataset")

_ALLOWED_EXT = (".xlsx", ".xls")
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # 100 MB


@router.post("/upload")
async def upload_dataset(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    """上传 Excel:**秒建一行(status=cleaning)立即返回**,重活(AI 表头识别 / 清洗 / parquet /
    ES 索引)全部丢后台。前端拿到 dataset_id 后靠轮询 status 等卡片变 ready/failed,不被阻塞。
    """
    filename = file.filename or "upload.xlsx"
    if not filename.lower().endswith(_ALLOWED_EXT):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xls 文件")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"文件超过 {mb}MB 限制")

    try:
        result = await ingest_excel(user_id=user_id, filename=filename, file_bytes=file_bytes)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"上传失败:{exc}")
        raise HTTPException(status_code=500, detail=f"入库失败:{exc}")


@router.get("")
async def list_datasets(user_id: str = Depends(get_current_user)):
    """列出当前用户自己的数据集(只返回归属调用者的,避免串号)。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        rows = await repo.list_by_user(user_id)
    return [
        {
            "dataset_id": r.id,
            "user_id": r.user_id,
            "name": r.name,
            "original_filename": r.original_filename,
            "status": r.status,
            "sheet_count": r.sheet_count,
            "total_rows": r.total_rows,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            # 失败时把后台记录的原因带给前端,卡片可提示(如"表头没对齐,请重传")
            "error_message": (r.schema_json or {}).get("_error") if r.status == "failed" else None,
        }
        for r in rows
    ]


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: int, user_id: str = Depends(get_current_user)):
    """查看一个数据集的详细 schema(仅限归属当前用户的数据集)。"""
    ds = await require_owned_dataset(dataset_id, user_id)
    return {
        "dataset_id": ds.id,
        "user_id": ds.user_id,
        "name": ds.name,
        "original_filename": ds.original_filename,
        "folder_path": ds.folder_path,
        "status": ds.status,
        "sheet_count": ds.sheet_count,
        "total_rows": ds.total_rows,
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
        "schema": ds.schema_json,
    }


@router.get("/{dataset_id}/header-review")
async def get_header_review(dataset_id: int, user_id: str = Depends(get_current_user)):
    """取「待确认表头」的预览载荷(各 sheet 前若干行网格 + 建议表头),供前端渲染确认弹窗。

    仅 status=needs_header 时有内容;其余状态返回 needs_review=False。
    """
    ds = await require_owned_dataset(dataset_id, user_id)
    review = (ds.schema_json or {}).get("_header_review") if ds.schema_json else None
    if ds.status != "needs_header" or not review:
        return {"dataset_id": dataset_id, "status": ds.status, "needs_review": False}
    # original_key 是内部对象存储路径,不外泄给前端
    sheets = {
        name: {k: v for k, v in info.items() if k != "original_key"}
        for name, info in (review.get("sheets") or {}).items()
    }
    return {
        "dataset_id": dataset_id,
        "status": ds.status,
        "needs_review": True,
        "filename": review.get("filename"),
        "sheets": sheets,
    }


@router.post("/{dataset_id}/header-confirm")
async def confirm_header(
    dataset_id: int,
    sheets: dict[str, dict] = Body(..., embed=True),
    user_id: str = Depends(get_current_user),
):
    """用户在确认弹窗里手选表头行后提交:校验后**后台重跑**解析入库,立即返回(前端轮询 status)。

    sheets = {"<sheet名>": {"data_start_row": <int>, "columns": [<str>...]}}。
    """
    ds = await require_owned_dataset(dataset_id, user_id)
    if ds.status != "needs_header":
        raise HTTPException(status_code=409, detail="该数据集当前不需要确认表头")
    if not sheets:
        raise HTTPException(status_code=400, detail="缺少表头选择")
    for name, spec in sheets.items():
        if not isinstance(spec, dict) or "data_start_row" not in spec or not spec.get("columns"):
            raise HTTPException(status_code=400, detail=f"sheet「{name}」的表头选择不完整")

    # 重活丢后台(含 ES 索引),立即返回;reprocess 内部会先把状态标回 cleaning
    asyncio.create_task(reprocess_with_headers(dataset_id, sheets))
    logger.info(f"[/dataset] user_id={user_id} 确认数据集 {dataset_id} 表头,后台重跑")
    return {"ok": True, "dataset_id": dataset_id, "status": "cleaning"}


@router.delete("/{dataset_id}")
async def delete_dataset_endpoint(dataset_id: int, user_id: str = Depends(get_current_user)):
    """删数据集:同步清 MySQL 行 / parquet 文件夹 / ES 文档(仅限归属当前用户的数据集)。"""
    # 先校验归属:不存在 / 不属于当前用户 → 404,绝不删别人的数据
    await require_owned_dataset(dataset_id, user_id)
    ok = await delete_dataset(dataset_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")
    # 连带清掉该数据集的问答会话历史(与删数据源一致)
    from repositories.conversation import ConversationRepository
    convs_removed = 0
    Session = get_session_factory()
    async with Session() as conv_session:
        async with conv_session.begin():
            convs_removed = await ConversationRepository(conv_session).delete_by_dataset(dataset_id)
    logger.info(f"[/dataset] user_id={user_id} 删除数据集 {dataset_id}(含会话 {convs_removed} 条)")
    return {"ok": True, "dataset_id": dataset_id}
