from conf.meta_config import ColumnConfig, TableConfig, MetricConfig, MetaConfig
import json
from dtos.meta import ColumnInfo, TableInfo, MetricInfo, ColumnMetric
from repositories.mysql import MetaDBRepository, DWDBRepository
from clients.mysql import meta_mysql_client, dw_mysql_client
from typing import Any


async def sync_dw_db():
    # 读取文件
    fp = open("conf/meta_config.json", "r", encoding="utf-8")
    #先把 meta_config.json 读成普通字典(config_dict)
    config_dict = json.load(fp)
    #再把这个字典config_dict转换为Pydantic 对象(meta_config)
    #meta_config 是一个 MetaConfig 实例
    meta_config = MetaConfig.model_validate(config_dict)
    fp.close()

    # 创建session
    async with (
        meta_mysql_client.session() as meta_session,
        dw_mysql_client.session() as dw_session
    ):
        meta_repo = MetaDBRepository(meta_session)
        dw_repo = DWDBRepository(dw_session)
        async with meta_session.begin():
            # 先清除元数据库所有数据
            await meta_repo.clear_all()
            
            # 1. 添加列和表到元数据库
            #定义了一个变量 table_infos，规定类型为 list[TableInfo]，用于存储从配置文件中读取的表信息。
            table_infos: list[TableInfo] = []
            for table in meta_config.tables:
                #定义一个变量 column_infos，规定类型为 list[ColumnInfo]，用于存储从配置文件中读取的列信息。
                column_infos: list[ColumnInfo] = []
                #每循环到一个表，就创建一个 TableInfo 对象，并将其添加到 table_infos 列表中。
                table_info = TableInfo(
                    id=table.name,
                    name=table.name,
                    role=table.role,
                    description=table.description
                )
                table_infos.append(table_info)
                #读取每个表的数据类型信息
                column_types: dict[str, str] = await dw_repo.get_column_types(table.name)
                #获取每个列的具体值
                column_values: dict[str, list[Any]] = await dw_repo.get_column_values(table.name,[column.name for column in table.columns])
                for column in table.columns:
                    values = column_values[column.name]
                    # examples 只需要唯一值，不需要重复记录
                    examples = list(dict.fromkeys(values))
                    column_info = ColumnInfo(
                        #id设置成表名加列名的形式，保证唯一性
                        id=f"{table_info.id}.{column.name}",
                        name=column.name,
                        #把key就是列名column.name传入就可以得到value，也就是数据类型
                        type=column_types[column.name],
                        role=column.role,
                        #得到该列名的所有值
                        examples=examples,
                        description=column.description,
                        alias=column.alias,
                        table_id=table_info.id
                    )
                    column_infos.append(column_info)
                # 添加所有列到数据库中
                await meta_repo.add_column_infos(column_infos)
            # 添加所有表到数据库中
            await meta_repo.add_table_infos(table_infos)
            # 2. 添加所有指标信息到元数据库
            metric_infos: list[MetricInfo] = []
            column_metrics: list[ColumnMetric] = []
            for metric in meta_config.metrics:
                metric_info = MetricInfo(
                    id=metric.name,
                    name=metric.name,
                    description=metric.description,
                    relevant_columns=metric.relevant_columns,
                    alias=metric.alias
                )
                metric_infos.append(metric_info)

                for relevant_column in metric.relevant_columns:
                    column_metric = ColumnMetric(
                        column_id=relevant_column,
                        metric_id=metric.name
                    )
                    column_metrics.append(column_metric)
            # 添加所有Metric信息到数据库中
            await meta_repo.add_metric_infos(metric_infos)
            # 添加所有ColumnMetric信息到数据库中
            await meta_repo.add_column_metrics(column_metrics)

    
# async def test():
#     from repositories.mysql import DWDBRepository
#     async with dw_mysql_client.session() as session:
#         async with session.begin():
#             dw_repo = DWDBRepository(session)
#             values = await dw_repo.get_column_values("table_product", ["product_name", 'product_id'])
#             # print(values)

async def main():
    # 1. 同步业务数据库结构信息到元数据库中
    await sync_dw_db()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main()) 