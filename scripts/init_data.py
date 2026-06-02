from conf.meta_config import MetaConfig
import json
from dtos.meta import ColumnInfo, TableInfo, MetricInfo, ColumnMetric
from dtos.qdrant import ColumnQdrantInfo, MetricQdrantInfo
from repositories.mysql import MetaDBRepository, DWDBRepository
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from clients.mysql import meta_mysql_client, dw_mysql_client
from clients.embedding import embedding_client
from clients.qdrant import qdrant_client
from typing import Any
from uuid import uuid4
from repositories.es import ESRepository
from clients.es import es_client
from dtos.es import ValueInfo

fp = open("conf/meta_config.json", "r", encoding="utf-8")
meta_config_dict = json.load(fp)
fp.close()
meta_config = MetaConfig.model_validate(meta_config_dict)

async def sync_dw_to_meta_db() -> tuple[list[ColumnInfo], list[MetricInfo]]:

    async with (
        meta_mysql_client.session() as meta_session,
        dw_mysql_client.session() as dw_session
    ):
        dw_repo = DWDBRepository(dw_session)
        meta_repo = MetaDBRepository(meta_session)

        async with meta_session.begin():
            await meta_repo.clear_all()
            # 1. 同步TableInfo和ColumnInfo到meta元数据库中
            table_infos: list[TableInfo] = []
            column_infos: list[ColumnInfo] = []

            for table in meta_config.tables:
                table_info = TableInfo(
                    id=table.name,
                    name=table.name,
                    role=table.role,
                    description=table.description
                )
                table_infos.append(table_info)

                column_types: dict[str, str] = await dw_repo.get_column_types(table.name)
                column_values: dict[str, list[Any]] = await dw_repo.get_column_values(table.name, [column.name for column in table.columns])

                for column in table.columns:
                    values = column_values[column.name]
                    examples = list(set(values))
                    column_info = ColumnInfo(
                        id=f"{table.name}.{column.name}",
                        name=column.name,
                        type=column_types[column.name],
                        role=column.role,
                        examples=examples,
                        description=column.description,
                        alias=column.alias,
                        table_id=table_info.id
                    )
                    column_infos.append(column_info)

            await meta_repo.add_column_infos(column_infos)
            await meta_repo.add_table_infos(table_infos)

            # 2. 同步MetricInfo和ColumnMetric到meta元数据库中
            metric_infos: list[MetricInfo] = []
            for metric in meta_config.metrics:
                metric_info: MetricInfo = MetricInfo(
                    id=metric.name,
                    name=metric.name,
                    description=metric.description,
                    relevant_columns=metric.relevant_columns,
                    alias=metric.alias
                )
                metric_infos.append(metric_info)

                column_metrics: list[ColumnMetric] = []
                for relevant_column in metric.relevant_columns:
                    column_metric = ColumnMetric(
                        column_id=relevant_column,
                        metric_id=metric_info.id
                    )
                    column_metrics.append(column_metric)
                await meta_repo.add_column_metrics(column_metrics)
            await meta_repo.add_metric_infos(metric_infos)

            return column_infos, metric_infos

#向量化 字段的名字丶描述和别名 便于向量化检索召回字段相关信息
async def sync_meta_column_to_qdrant(column_infos: list[ColumnInfo]):
    column_qdrant_infos: list[ColumnQdrantInfo] = []
    for column_info in column_infos:
        texts = [column_info.name, column_info.description] + column_info.alias
        # 用分批方法：alias 多到超过服务端上限时自动切片并行，避免 400
        embeddings_list = await embedding_client.aembed_documents_batched(texts)
        #分别量化防止破坏语义信息，保证每个文本都能得到一个独立的向量表示。
        for embeddings in embeddings_list:
            column_qdrant_infos.append(
                ColumnQdrantInfo(
                    id=str(uuid4()),
                    #储存字段相关的向量信息
                    embeddings=embeddings,
                    #payload储存字段的具体信息
                    payload=column_info
                )
            )
    #调用ColumnQdrantRepository的upsert方法
    qdrant_repo = ColumnQdrantRepository(qdrant_client.client)
    await qdrant_repo.clear_all()
    await qdrant_repo.ensure_collection()
    await qdrant_repo.upsert(column_qdrant_infos)

#向量化指标的名字丶描述和别名 便于向量化检索召回指标相关信息
async def sync_meta_metric_to_qdrant(metric_infos: list[MetricInfo]):
    metric_qdrant_infos: list[MetricQdrantInfo] = []
    for metric_info in metric_infos:
        texts = [metric_info.name, metric_info.description] + metric_info.alias
        # 用分批方法：alias 多到超过服务端上限时自动切片并行，避免 400
        embeddings_list = await embedding_client.aembed_documents_batched(texts)
        for embeddings in embeddings_list:
            metric_qdrant_infos.append(
                MetricQdrantInfo(
                    id=str(uuid4()),
                    embeddings=embeddings,
                    payload=metric_info
                )
            )

    qdrant_repo = MetricQdrantRepository(qdrant_client.client)
    await qdrant_repo.clear_all()
    await qdrant_repo.ensure_collection()
    await qdrant_repo.upsert(metric_qdrant_infos)

async def sync_dw_value_to_es(column_infos: list[ColumnInfo]):
    es_repo = ESRepository(es_client.client)
    # 1. 清除es中所有的数据
    await es_repo.clear_all()
    # 2. 确保创建了index
    await es_repo.ensure_index()
    #循环所有表的字段，根据column.sync的配置决定是否同步该字段的数据到es中。
    value_infos: list[ValueInfo] = []
    async with dw_mysql_client.session() as session:
        dw_repo = DWDBRepository(session)
        index: int = 0
        for table in meta_config.tables:
            for column in table.columns:
                #遍历
                column_info = column_infos[index]
                if column.sync:
                    values_dict: dict[str, list[Any]] = await dw_repo.get_column_values(table.name, [column.name], limit=100000)
                    values: list = values_dict[column.name]
                    the_value_infos = [
                        ValueInfo(
                            id=str(uuid4()),
                            value=value,
                            column_id=column_info.id
                        )
                        for value in values
                    ]
                    value_infos.extend(the_value_infos)
                index += 1
    # 批量将value_infos添加到es中
    await es_repo.add_documents(value_infos)


async def main():
    try:
        # 1. 同步业务数据库结构到元数据库中
        column_infos, metric_infos = await sync_dw_to_meta_db()
        # 2. 将元数据库的字段同步到qdrant中
        await sync_meta_column_to_qdrant(column_infos)
        # 3. 将元数据库中的指标同步到qdrant中
        await sync_meta_metric_to_qdrant(metric_infos)
        # 4. 将指定字段的值同步到es中建立索引
        await sync_dw_value_to_es(column_infos)
    finally:
        await es_client.close()
        await qdrant_client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())