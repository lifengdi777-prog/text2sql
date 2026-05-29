"""数据集相关接口:上传 / 列表 / 详情 / 删除。"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from core.log import logger
from repositories.upload import UploadDatasetRepository
from services.excel_ingest import delete_dataset, get_session_factory, ingest_excel

router = APIRouter(prefix="/dataset")

_ALLOWED_EXT = (".xlsx", ".xls")
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # 100 MB


@router.post("/upload")
async def upload_dataset(
    file: UploadFile = File(...),
    user_id: str = Form("anonymous"),
):
    """上传 Excel → 自动清洗 → 入 MySQL + parquet → 后台建 ES 值索引。"""
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
async def list_datasets(user_id: str | None = None):
    """列出数据集(可按 user_id 过滤)。"""
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
        }
        for r in rows
    ]


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: int):
    """查看一个数据集的详细 schema。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        ds = await repo.get(dataset_id)
        if ds is None:
            raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")
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


@router.delete("/{dataset_id}")
async def delete_dataset_endpoint(dataset_id: int):
    """删数据集:同步清 MySQL 行 / parquet 文件夹 / ES 文档。"""
    ok = await delete_dataset(dataset_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")
    return {"ok": True, "dataset_id": dataset_id}
