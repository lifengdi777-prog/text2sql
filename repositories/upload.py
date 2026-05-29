"""上传数据集的 Repository:upload_datasets 表的 CRUD。

注:schema_json 是 MySQL 的 JSON 列,SQLAlchemy 返回的是已经反序列化的 dict,
写入时直接传 dict 即可(SQLAlchemy + asyncmy 自动 json.dumps)。
"""
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.upload import UploadDatasetMySQL


class UploadDatasetRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: str,
        name: str,
        original_filename: str | None,
        status: str = "cleaning",
        content_hash: str | None = None,
    ) -> UploadDatasetMySQL:
        """先插入一行拿自增 id(此时 folder_path / schema_json 暂空),后续再 update。"""
        ds = UploadDatasetMySQL(
            user_id=user_id,
            name=name,
            original_filename=original_filename,
            status=status,
            content_hash=content_hash,
        )
        self.session.add(ds)
        await self.session.flush()
        return ds

    async def find_existing(
        self,
        user_id: str,
        original_filename: str | None,
        content_hash: str,
    ) -> UploadDatasetMySQL | None:
        """查同用户 + 同文件名 + 同内容 hash 的已就绪数据集。命中 → 上传去重。"""
        stmt = (
            select(UploadDatasetMySQL)
            .where(
                UploadDatasetMySQL.user_id == user_id,
                UploadDatasetMySQL.original_filename == original_filename,
                UploadDatasetMySQL.content_hash == content_hash,
                UploadDatasetMySQL.status == "ready",
            )
            .order_by(UploadDatasetMySQL.id.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def finalize(
        self,
        dataset_id: int,
        folder_path: str,
        schema_json: dict[str, Any],
        sheet_count: int,
        total_rows: int,
    ) -> None:
        ds = await self.session.get(UploadDatasetMySQL, dataset_id)
        if ds is None:
            return
        ds.folder_path = folder_path
        ds.schema_json = schema_json
        ds.sheet_count = sheet_count
        ds.total_rows = total_rows
        # parquet/schema 已就绪,但 ES 值索引还在后台建 → 先标 indexing,
        # 等 build_es_index_background 结束(成功/无值/失败)再置 ready。
        ds.status = "indexing"

    async def update_status(self, dataset_id: int, status: str) -> None:
        ds = await self.session.get(UploadDatasetMySQL, dataset_id)
        if ds is not None:
            ds.status = status

    async def get(self, dataset_id: int) -> UploadDatasetMySQL | None:
        return await self.session.get(UploadDatasetMySQL, dataset_id)

    async def get_schema(self, dataset_id: int) -> dict[str, Any] | None:
        """只取 schema_json,避免拉全行。"""
        stmt = select(UploadDatasetMySQL.schema_json).where(UploadDatasetMySQL.id == dataset_id)
        return await self.session.scalar(stmt)

    async def list_by_user(self, user_id: str | None = None) -> list[UploadDatasetMySQL]:
        stmt = select(UploadDatasetMySQL).order_by(UploadDatasetMySQL.id.desc())
        if user_id:
            stmt = stmt.where(UploadDatasetMySQL.user_id == user_id)
        return list((await self.session.scalars(stmt)).all())

    async def delete(self, dataset_id: int) -> None:
        await self.session.execute(
            delete(UploadDatasetMySQL).where(UploadDatasetMySQL.id == dataset_id)
        )
