"""一键初始化上传功能所需的基础设施。

运行:uv run python -m scripts.init_upload

会做 3 件事:
  1. CREATE DATABASE IF NOT EXISTS `upload`
  2. 在 upload 库里建 upload_datasets 表
  3. 在 ES 创建 upload_value_info 索引(ik_max_word 分词,跟 DW 的 value_info 一致)
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from clients.es import es_client
from conf.app_config import app_config
from models import Base
# 必须 import 模型,把表注册到 Base.metadata
from models.upload import UploadDatasetMySQL  # noqa: F401
from models.user import UserMySQL  # noqa: F401
from models.conversation import ConversationMySQL, MessageMySQL  # noqa: F401
from repositories.es import UploadESRepository


async def _init_mysql() -> None:
    cfg = app_config.db_upload
    if cfg is None:
        raise RuntimeError("请先在 app_config.yaml 配置 db_upload")

    # 1) 用无 database 的 admin 引擎建库
    admin = create_async_engine(
        f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/?charset=utf8mb4"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` CHARACTER SET utf8mb4")
            )
            await conn.commit()
    finally:
        await admin.dispose()
    print(f"[OK] MySQL 库 `{cfg.database}` 已就绪")

    # 2) 连进 upload 库建表 + 增量迁移(给老表补新列)
    engine = create_async_engine(
        f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.database}?charset=utf8mb4"
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # 幂等迁移:老表可能没 content_hash 列,补上
        await _migrate_add_column(engine, "upload_datasets", "content_hash",
                                  "VARCHAR(64) NULL COMMENT 'SHA-256 of original file'")
        await _migrate_add_index(engine, "upload_datasets", "idx_user_name_hash",
                                 "(user_id, original_filename, content_hash)")
    finally:
        await engine.dispose()
    print(f"[OK] 表 upload_datasets / users / conversations / messages 已就绪")


async def _migrate_add_column(engine, table: str, column: str, ddl_type: str) -> None:
    """如果列不存在就 ADD COLUMN。幂等,可重复跑。"""
    async with engine.connect() as conn:
        exists = await conn.scalar(text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ), {"t": table, "c": column})
        if exists:
            print(f"     列 {table}.{column} 已存在,跳过")
            return
        await conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl_type}"))
        await conn.commit()
        print(f"     已添加列 {table}.{column}")


async def _migrate_add_index(engine, table: str, index_name: str, cols_expr: str) -> None:
    """如果索引不存在就 ADD INDEX。幂等。"""
    async with engine.connect() as conn:
        exists = await conn.scalar(text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
        ), {"t": table, "i": index_name})
        if exists:
            print(f"     索引 {table}.{index_name} 已存在,跳过")
            return
        await conn.execute(text(f"ALTER TABLE `{table}` ADD INDEX `{index_name}` {cols_expr}"))
        await conn.commit()
        print(f"     已添加索引 {table}.{index_name}")


async def _init_es() -> None:
    repo = UploadESRepository(es_client.client)
    await repo.ensure_index()
    print(f"[OK] ES 索引 `{repo.index_name}` 已就绪 (ik_max_word 分词)")


async def main() -> None:
    try:
        await _init_mysql()
        await _init_es()
        print("\n上传基础设施初始化完成。")
    finally:
        await es_client.close()


if __name__ == "__main__":
    asyncio.run(main())
