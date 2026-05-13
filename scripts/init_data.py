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
        # 1. 同步TableInfo和ColumnInfo
        # 初始化dw和meta的repository对象
        dw_repo = DWDBRepository(dw_session)
        meta_repo = MetaDBRepository(meta_session)

        table_infos: list[TableInfo] = []
        # Tips: 这里调整了一下，从tables的for循环中调整到上层了
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
            
            #从meta_config.json中读取每个表的列的数据，
            #并将这些数据用ColumnInfo对象表示出来，
            # 最后把这些对象添加到column_infos列表中。
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
        
        # Tips: 这里调整了一下，从tables循环中放到tables循环结束后
        # 将当前表的所有列的信息存储到元数据库中
        await meta_repo.add_column_infos(column_infos)
        # 统一将所有table_infos添加到数据库中
        await meta_repo.add_table_infos(table_infos)

        # 2. 同步MetricInfo和ColumnMetric到元数据库中
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
            # 添加column_metrics到元数据库中
            await meta_repo.add_column_metrics(column_metrics)
        # 统一将所有metric_infos添加到元数据库中
        await meta_repo.add_metric_infos(metric_infos)

        # Tips：这里调整了一下，返回column_infos和metric_infos
        #column_infos和metric_infos是一个对象列表
        return column_infos, metric_infos

    
async def sync_meta_column_to_qdrant(column_infos: list[ColumnInfo]):
    column_qdrant_infos: list[ColumnQdrantInfo] = []
    for column_info in column_infos:
        texts = [column_info.name, column_info.description] + column_info.alias
        embeddings_list = await embedding_client.client.aembed_documents(texts)
        for embeddings in embeddings_list:
            column_qdrant_infos.append(
                ColumnQdrantInfo(
                    id=str(uuid4()),
                    embeddings=embeddings,
                    payload=column_info
                )
            )
    #qdrant_client.client是调用QDrantClient类中的AsyncQdrantClient对象。
    qdrant_repo = ColumnQdrantRepository(qdrant_client.client)
    #在子方法里面写好了collection_name，所以这里不需要传collection_name参数了。
    await qdrant_repo.clear_all()
    await qdrant_repo.ensure_collection()
    await qdrant_repo.upsert(column_qdrant_infos)


async def sync_meta_metric_to_qdrant(metric_infos: list[MetricInfo]):
    metric_qdrant_infos: list[MetricQdrantInfo] = []
    for metric_info in metric_infos:
        texts = [metric_info.name, metric_info.description] + metric_info.alias
        #一次性将一个列表的文本转换成一组嵌入向量
        embeddings_list = await embedding_client.client.aembed_documents(texts)
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
                #根据index索引从column_infos中取出对应的column_info对象。
                column_info = column_infos[index]
                if column.sync:
                    values_dict: dict[str, list[Any]] = await dw_repo.get_column_values(table.name, [column.name], limit=100000)
                    #用column.name作为key从values_dict中取出对应的值列表，然后为每个值创建一个ValueInfo对象，最后把这些对象添加到value_infos列表中。
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