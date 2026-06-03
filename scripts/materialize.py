"""把确认后的草稿物化到一个数据源:写 5 张 meta 表 + Qdrant + ES,全带 datasource_id,**增量**(只动该源)。

这是 init_data 的"单源增量版":
- init_data = 整库重建(drop 表),只适合 ds_default 初始化;
- materialize = 只 clear 本 datasource_id 的行/点/文档,再写入,绝不动别的源、不 drop 表。

跑法:  uv run python -m scripts.materialize <datasource_id> [config_path]
       config_path 不传则用 conf/meta_config.draft.<id>.json(generate_draft 的产物)
前提:  该 datasource 已注册(register_datasource),且草稿已生成(可手改后再物化)。
"""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from clients.embedding import embedding_client
from clients.es import es_client
from clients.mysql import client_registry, dw_mysql_client, meta_mysql_client
from clients.qdrant import qdrant_client
from conf.app_config import DEFAULT_DATASOURCE_ID
from conf.meta_config import MetaConfig
from dtos.es import ValueInfo
from dtos.meta import ColumnInfo, ColumnMetric, DataRelationship, MetricInfo, TableInfo
from dtos.qdrant import ColumnQdrantInfo, MetricQdrantInfo
from repositories.es import ESRepository
from repositories.mysql import DWDBRepository, MetaDBRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository


def _draft_path(datasource_id: str) -> Path:
    if datasource_id == DEFAULT_DATASOURCE_ID:
        return Path("conf/meta_config.draft.json")
    return Path(f"conf/meta_config.draft.{datasource_id}.json")


# ── 1. 用 config + DW 连接,构建要写入的 meta 行(全带 datasource_id) ──────────
async def _build_meta(datasource_id: str, config: MetaConfig, dw_repo: DWDBRepository):
    table_infos: list[TableInfo] = []
    column_infos: list[ColumnInfo] = []
    for table in config.tables:
        table_infos.append(TableInfo(
            id=table.name, name=table.name, role=table.role,
            description=table.description, datasource_id=datasource_id,
        ))
        column_types = await dw_repo.get_column_types(table.name)
        column_values = await dw_repo.get_column_values(table.name, [c.name for c in table.columns])
        for column in table.columns:
            column_infos.append(ColumnInfo(
                id=f"{table.name}.{column.name}", name=column.name,
                type=column_types[column.name], role=column.role,
                examples=list(set(column_values[column.name])),
                description=column.description, alias=column.alias,
                table_id=table.name, datasource_id=datasource_id,
            ))

    # 关系边走该库声明的外键(命名推断的兜底已在 introspect,这里以 DB 真实约束为准)
    col_desc = {ci.id: ci.description for ci in column_infos}
    fks = await dw_repo.get_foreign_keys()
    relationships = [DataRelationship(
        from_table=fk["from_table"], from_column=fk["from_column"],
        to_table=fk["to_table"], to_column=fk["to_column"],
        description=col_desc.get(f'{fk["from_table"]}.{fk["from_column"]}'),
        datasource_id=datasource_id,
    ) for fk in fks]

    metric_infos: list[MetricInfo] = []
    column_metrics: list[ColumnMetric] = []
    for metric in config.metrics:
        metric_infos.append(MetricInfo(
            id=metric.name, name=metric.name, description=metric.description,
            relevant_columns=metric.relevant_columns, alias=metric.alias,
            datasource_id=datasource_id,
        ))
        for rc in metric.relevant_columns:
            column_metrics.append(ColumnMetric(column_id=rc, metric_id=metric.name, datasource_id=datasource_id))

    return table_infos, column_infos, metric_infos, column_metrics, relationships


async def _build_values(datasource_id: str, config: MetaConfig,
                        column_infos: list[ColumnInfo], dw_repo: DWDBRepository) -> list[ValueInfo]:
    """sync=true 的列,取其真实值(最多 10 万)做 ES 值索引。"""
    value_infos: list[ValueInfo] = []
    idx = 0
    for table in config.tables:
        for column in table.columns:
            ci = column_infos[idx]
            idx += 1
            if column.sync:
                values = (await dw_repo.get_column_values(table.name, [column.name], limit=100000))[column.name]
                value_infos.extend(
                    ValueInfo(id=str(uuid4()), value=v, column_id=ci.id, datasource_id=datasource_id)
                    for v in values
                )
    return value_infos


# ── 2. 写入(都是"只动本源"的增量) ──────────────────────────────────────────
async def _write_meta(datasource_id, table_infos, column_infos, metric_infos, column_metrics, relationships):
    async with meta_mysql_client.session() as session:
        repo = MetaDBRepository(session, datasource_id)
        async with session.begin():
            await repo.clear_all()  # 只清本 datasource_id 的 5 张表行
            await repo.add_column_infos(column_infos)
            await repo.add_table_infos(table_infos)
            await repo.add_relationships(relationships)
            await repo.add_column_metrics(column_metrics)
            await repo.add_metric_infos(metric_infos)


async def _sync_qdrant(datasource_id, column_infos: list[ColumnInfo], metric_infos: list[MetricInfo]):
    # 列
    col_repo = ColumnQdrantRepository(qdrant_client.client)
    await col_repo.ensure_collection()
    await col_repo.delete_by_datasource(datasource_id)  # 先清本源旧向量
    col_points: list[ColumnQdrantInfo] = []
    for ci in column_infos:
        texts = [ci.name, ci.description] + ci.alias
        for emb in await embedding_client.aembed_documents_batched(texts):
            col_points.append(ColumnQdrantInfo(id=str(uuid4()), embeddings=emb, payload=ci))
    await col_repo.upsert(col_points)

    # 指标
    metric_repo = MetricQdrantRepository(qdrant_client.client)
    await metric_repo.ensure_collection()
    await metric_repo.delete_by_datasource(datasource_id)
    metric_points: list[MetricQdrantInfo] = []
    for mi in metric_infos:
        texts = [mi.name, mi.description] + mi.alias
        for emb in await embedding_client.aembed_documents_batched(texts):
            metric_points.append(MetricQdrantInfo(id=str(uuid4()), embeddings=emb, payload=mi))
    await metric_repo.upsert(metric_points)


async def _sync_es(datasource_id, value_infos: list[ValueInfo]):
    es_repo = ESRepository(es_client.client)
    await es_repo.ensure_index()
    await es_repo.delete_by_datasource(datasource_id)  # 先清本源旧值
    await es_repo.add_documents(value_infos)


async def main():
    if len(sys.argv) < 2:
        print("用法: uv run python -m scripts.materialize <datasource_id> [config_path]")
        return
    datasource_id = sys.argv[1]
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _draft_path(datasource_id)
    if not config_path.exists():
        print(f"找不到配置文件: {config_path}(先跑 generate_draft 生成草稿)")
        return
    config = MetaConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    print(f"数据源: {datasource_id}  配置: {config_path}")

    try:
        # 需要 DW 连接的活全在这一个会话里做完
        client = await client_registry.get_client(datasource_id)
        async with client.session() as session:
            dw_repo = DWDBRepository(session)
            table_infos, column_infos, metric_infos, column_metrics, relationships = \
                await _build_meta(datasource_id, config, dw_repo)
            value_infos = await _build_values(datasource_id, config, column_infos, dw_repo)
        print(f"  构建: {len(table_infos)} 表 / {len(column_infos)} 列 / {len(metric_infos)} 指标 / "
              f"{len(relationships)} 关系 / {len(value_infos)} 个待索引值")

        await _write_meta(datasource_id, table_infos, column_infos, metric_infos, column_metrics, relationships)
        print("  ✓ meta 5 张表已写入(本源增量)")
        await _sync_qdrant(datasource_id, column_infos, metric_infos)
        print("  ✓ Qdrant 列/指标向量已写入(payload 带 datasource_id)")
        await _sync_es(datasource_id, value_infos)
        print("  ✓ ES 值索引已写入(doc 带 datasource_id)")
        print(f"[OK] 数据源 {datasource_id} 物化完成,可问数了。")
    finally:
        await client_registry.close_all()
        await dw_mysql_client.close()
        await es_client.close()
        await qdrant_client.close()


if __name__ == "__main__":
    asyncio.run(main())
