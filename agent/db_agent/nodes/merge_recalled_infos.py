from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo, WSAgentTableInfoState
from dtos.meta import TableInfo, ColumnInfo, MetricInfo, ValueInfo
from agent.db_agent.join_path import complete_join_path
from core.log import logger


async def merge_recalled_infos(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="合并召回信息", status="running"))
    #有数据返回数据，没有数据为空列表
    recalled_columns = state.recalled_columns or []
    recalled_metrics = state.recalled_metrics or []
    recalled_values = state.recalled_values or []

    # 1. 合并表信息
    # 整段 1.1~1.5 用一个短会话:这中间全是字典/循环 + 几次 DB 读,无 LLM 等待,
    # 一个会话快速跑完即归还连接(不像以前那样被整条流全程占着)。
    async with runtime.context.meta_repo() as meta_db_repo:
        # 1.1. 将召回的字段以及指标关联的字段都合并在一起
        # 如果召回的字段里面没有相关联的字段信息，就从数据库中提取相关联的字段信息
        recalled_column_mapping: dict[str, ColumnInfo] = {
            # 定义一个字典recalled_column_mapping，用于存储召回的字段信息，键是字段ID，值是ColumnInfo对象。
            column_info.id: column_info
            for column_info in recalled_columns
        }

        for recalled_metric in recalled_metrics:
            # 该指标关联的字段ID列表
            relevant_columns = recalled_metric.relevant_columns
            # 遍历相关字段ID列表，如果字段ID不在recalled_column_mapping中，说明之前没有被召回过，
            # 就从数据库中提取该字段信息并存放到recalled_column_mapping中。
            for relevant_column_id in relevant_columns:
                if relevant_column_id not in recalled_column_mapping:
                    # 从数据库中提取字段
                    column_info = await meta_db_repo.get_column_info_by_id(relevant_column_id)
                    if column_info is not None:
                        recalled_column_mapping[relevant_column_id] = column_info

        # 1.2. 将召回的es值存放到对应字段的exemples中
        for recalled_value in recalled_values:
            #如果召回值的id不在recalled_column_mapping中，（recalled_value.column_id=表.字段）
            # 说明之前没有被召回过，就从数据库中提取该字段信息并存放到recalled_column_mapping中。
            column_id = recalled_value.column_id
            if column_id not in recalled_column_mapping:
                # 从数据库中提取字段
                column_info = await meta_db_repo.get_column_info_by_id(column_id)
                if column_info is not None:
                    recalled_column_mapping[column_id] = column_info
                else:
                    continue
            #如果ES召回的value不在column_info的examples里，就添加进去。
            column_info = recalled_column_mapping[column_id]
            if recalled_value.value not in column_info.examples:
                column_info.examples.append(recalled_value.value)

        # 1.3. 将所有字段放到对应的表下。就是把列，按照所属的表，归类放在一起
        #键是表ID，值是字段信息列表。
        table_columns_mapping: dict[str, list[ColumnInfo]] = {}
        for column_info in recalled_column_mapping.values():
            #按 table_id 分组字段信息，构建表与字段的映射关系。
            table_id = column_info.table_id
            if table_id not in table_columns_mapping:
                table_columns_mapping[table_id] = [column_info]
            else:
                table_columns_mapping[table_id].append(column_info)

        # 1.4. 为表补充主键和外键（大模型可能有时候识别不出主键和外键）
        for table_id in table_columns_mapping.keys():
            # 查找数据库获取表的主外键信息，并补充到table_columns_mapping中
            column_infos = await meta_db_repo.get_table_pfks_by_id(table_id)
            # 先获取当前table表已经存储的所有的字段id（column_info.id）
            table_column_ids = [
                column_info.id
                for column_info in table_columns_mapping[table_id]
            ]
            #遍历数据库查出的主外键，如果当前table表还没有，就补充进去
            for column_info in column_infos:
                if column_info.id not in table_column_ids:
                    table_columns_mapping[table_id].append(column_info)

        # 1.5. 将table_columns_mapping转换为WSAgentTableInfoState
        # 第一步：定义空列表，准备存放结果
        table_infos: list[WSAgentTableInfoState] = []
        # 第二步：遍历每张表
        for table_id, column_infos in table_columns_mapping.items():
            # 第三步：去数据库查这张表的基本信息（名称、描述、角色）
            table_info = await meta_db_repo.get_table_info_by_id(table_id)
            # 第四步：查到了才打包（查不到就跳过）
            if table_info:
                table_infos.append(WSAgentTableInfoState(
                    id=table_id,
                    name=table_info.name, #额外加入的表信息
                    description=table_info.description, #额外加入的表的描述信息
                    role=table_info.role,  #额外加入的表的角色信息（维表还是事实表）
                    columns=column_infos  # 召回的字段 + 指标/值关联的字段 + 补充的主外键
                ))

        # 1.6 连接路径补全:把"已选表→事实表"路径上缺失的中间表补进来,
        #     避免雪花多跳维表(如 city 所在的 factory)因中间表(workshop)未被召回而成孤岛、
        #     在 filter 阶段被当作"连不上的废表"误删(详见 join_path.complete_join_path)。
        table_infos = await complete_join_path(table_infos, meta_db_repo)

    # 2. 处理指标信息
    metric_infos: list[MetricInfo] = [metric_info for metric_info in recalled_metrics]

    writer(WSStepInfo(step="合并召回信息", status="success"))

    logger.info([
        (table_info.name, [column_info.name for column_info in table_info.columns])
        for table_info in table_infos
    ])
    logger.info([metric.name for metric in metric_infos])

    return {"table_infos": table_infos, "metric_infos": metric_infos}
