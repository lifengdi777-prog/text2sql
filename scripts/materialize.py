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


# ── 构建各部分的 meta 行(全带 datasource_id) ──────────────────────────────
async def _build_tables(datasource_id, tables, dw_repo: DWDBRepository):
    """从 DW 读类型/采样,构建 table_info + column_info。"""
    table_infos: list[TableInfo] = []
    column_infos: list[ColumnInfo] = []
    for table in tables:
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
                table_id=table.name, sync=column.sync, datasource_id=datasource_id,
            ))
    return table_infos, column_infos


def _build_metrics(datasource_id, metrics):
    """构建 metric_info + column_metric(纯 config,不连 DW)。"""
    metric_infos: list[MetricInfo] = []
    column_metrics: list[ColumnMetric] = []
    for metric in metrics:
        metric_infos.append(MetricInfo(
            id=metric.name, name=metric.name, description=metric.description,
            relevant_columns=metric.relevant_columns, alias=metric.alias,
            datasource_id=datasource_id,
        ))
        for rc in metric.relevant_columns:
            column_metrics.append(ColumnMetric(column_id=rc, metric_id=metric.name, datasource_id=datasource_id))
    return metric_infos, column_metrics


async def _build_values(datasource_id, tables, column_infos: list[ColumnInfo], dw_repo: DWDBRepository):
    """sync=true 的列,取其真实值(最多 10 万)做 ES 值索引。"""
    value_infos: list[ValueInfo] = []
    idx = 0
    for table in tables:
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


async def _sync_column_qdrant(datasource_id, column_infos: list[ColumnInfo]):
    repo = ColumnQdrantRepository(qdrant_client.client)
    await repo.ensure_collection()
    await repo.delete_by_datasource(datasource_id)
    points: list[ColumnQdrantInfo] = []
    for ci in column_infos:
        texts = [ci.name, ci.description] + ci.alias
        for emb in await embedding_client.aembed_documents_batched(texts):
            points.append(ColumnQdrantInfo(id=str(uuid4()), embeddings=emb, payload=ci))
    await repo.upsert(points)


async def _sync_metric_qdrant(datasource_id, metric_infos: list[MetricInfo]):
    repo = MetricQdrantRepository(qdrant_client.client)
    await repo.ensure_collection()
    await repo.delete_by_datasource(datasource_id)
    points: list[MetricQdrantInfo] = []
    for mi in metric_infos:
        texts = [mi.name, mi.description] + mi.alias
        for emb in await embedding_client.aembed_documents_batched(texts):
            points.append(MetricQdrantInfo(id=str(uuid4()), embeddings=emb, payload=mi))
    await repo.upsert(points)


async def _sync_es(datasource_id, value_infos: list[ValueInfo]):
    es_repo = ESRepository(es_client.client)
    await es_repo.ensure_index()
    await es_repo.delete_by_datasource(datasource_id)
    await es_repo.add_documents(value_infos)


# ── 三块独立物化:各自只动自己那部分 MySQL + 对应索引 ──────────────────────
async def materialize_tables(datasource_id: str, tables) -> dict:
    """保存「表元数据」:写 table_info/column_info + 重嵌列向量 + 重灌 ES。不碰指标/关系。"""
    client = await client_registry.get_client(datasource_id)
    async with client.session() as session:
        dw_repo = DWDBRepository(session)
        table_infos, column_infos = await _build_tables(datasource_id, tables, dw_repo)
        value_infos = await _build_values(datasource_id, tables, column_infos, dw_repo)
    async with meta_mysql_client.session() as session:
        repo = MetaDBRepository(session, datasource_id)
        async with session.begin():
            await repo.clear_tables()
            await repo.add_column_infos(column_infos)
            await repo.add_table_infos(table_infos)
    await _sync_column_qdrant(datasource_id, column_infos)
    await _sync_es(datasource_id, value_infos)
    return {"tables": len(table_infos), "columns": len(column_infos), "values": len(value_infos)}


async def materialize_metrics(datasource_id: str, metrics) -> dict:
    """保存「指标信息」:写 metric_info/column_metric + 重嵌指标向量。不碰表/列/ES/关系。"""
    metric_infos, column_metrics = _build_metrics(datasource_id, metrics)
    async with meta_mysql_client.session() as session:
        repo = MetaDBRepository(session, datasource_id)
        async with session.begin():
            await repo.clear_metrics()
            await repo.add_column_metrics(column_metrics)
            await repo.add_metric_infos(metric_infos)
    await _sync_metric_qdrant(datasource_id, metric_infos)
    return {"metrics": len(metric_infos)}


async def materialize(datasource_id: str, config: MetaConfig, rebuild_relationships: bool = True) -> dict:
    """首次导入:全量物化(表 + 指标 + 关系)。供 build / CLI 用。

    rebuild_relationships=True 时从该库声明的外键种一次 data_relationship;
    编辑保存走 materialize_tables / materialize_metrics / replace_relationships 三条独立路径。
    **不关闭任何进程级客户端**,清理由 CLI 的 main() 负责。
    """
    t = await materialize_tables(datasource_id, config.tables)
    m = await materialize_metrics(datasource_id, config.metrics)

    rel_count = 0
    if rebuild_relationships:
        selected = {tt.name for tt in config.tables}
        col_desc = {f"{tt.name}.{c.name}": c.description for tt in config.tables for c in tt.columns}
        client = await client_registry.get_client(datasource_id)
        async with client.session() as session:
            fks = await DWDBRepository(session).get_foreign_keys()
        rels = [DataRelationship(
            from_table=fk["from_table"], from_column=fk["from_column"],
            to_table=fk["to_table"], to_column=fk["to_column"],
            description=col_desc.get(f'{fk["from_table"]}.{fk["from_column"]}'),
            datasource_id=datasource_id,
        ) for fk in fks if fk["from_table"] in selected and fk["to_table"] in selected]
        async with meta_mysql_client.session() as session:
            async with session.begin():
                await MetaDBRepository(session, datasource_id).replace_relationships(rels)
        rel_count = len(rels)

    return {**t, **m, "relationships": rel_count}


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
        stats = await materialize(datasource_id, config)
        print(f"  构建: {stats['tables']} 表 / {stats['columns']} 列 / {stats['metrics']} 指标 / "
              f"{stats['relationships']} 关系 / {stats['values']} 个待索引值")
        print(f"[OK] 数据源 {datasource_id} 物化完成,可问数了。")
    finally:
        await client_registry.close_all()
        await dw_mysql_client.close()
        await es_client.close()
        await qdrant_client.close()


if __name__ == "__main__":
    asyncio.run(main())
