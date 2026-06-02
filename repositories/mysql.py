from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from models.meta import ColumnInfoMySQL, MetricInfoMySQL, TableInfoMySQL, ColumnMetricMySQL
from dtos.meta import ColumnInfo, TableInfo, MetricInfo, ColumnMetric
from sqlalchemy import text, and_, or_
from typing import Any

# MetaDBRepository类用于操作元数据库中的表格和指标信息。
class MetaDBRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def clear_all(self):
        await self.session.execute(delete(ColumnMetricMySQL))
        await self.session.execute(delete(MetricInfoMySQL))
        await self.session.execute(delete(ColumnInfoMySQL))
        await self.session.execute(delete(TableInfoMySQL))

#以下方法用于将ColumnInfo、TableInfo、MetricInfo和ColumnMetric对象添加到数据库中。
# 每个方法都接受一个包含相应对象的列表，并将这些对象转换为对应的MySQL模型实例，
# 然后使用session.add_all()方法将它们添加到数据库会话中。
#column_infos指的是一批要写入 meta 数据库的“列信息 DTO”对象。
    async def add_column_infos(self, column_infos: list[ColumnInfo]):
        #ColumnInfoMySQL(**column_info.model_dump())的作用是将ColumnInfo对象转换为一个字典，
        # 然后使用这个字典来创建一个ColumnInfoMySQL实例。
        #这里是批量将ColumnInfo对象转换为ColumnInfoMySQL对象，并将它们添加到数据库会话中。
        self.session.add_all([ColumnInfoMySQL(**column_info.model_dump()) for column_info in column_infos])

    async def add_table_infos(self, table_infos: list[TableInfo]):
        self.session.add_all([TableInfoMySQL(**table_info.model_dump()) for table_info in table_infos])

    async def add_metric_infos(self, metric_infos: list[MetricInfo]):
        self.session.add_all([MetricInfoMySQL(**metric_info.model_dump()) for metric_info in metric_infos])

    async def add_column_metrics(self, column_metrics: list[ColumnMetric]):
        self.session.add_all([ColumnMetricMySQL(**column_metric.model_dump()) for column_metric in column_metrics])

    async def get_column_info_by_id(self, column_id: str) -> ColumnInfo | None:
        stmt = select(ColumnInfoMySQL).where(ColumnInfoMySQL.id == column_id)
        column_info_mysql = await self.session.scalar(stmt)
        if column_info_mysql:
            #返回相应的ColumnInfo对象，如果没有找到对应的记录，则返回None。
            return ColumnInfo.model_validate(column_info_mysql)
        return None
    
    # 根据table_id获取表的主外键信息
    async def get_table_pfks_by_id(self, table_id: str) -> list[ColumnInfo]:
        # 根据table_id获取表的主外键
        stmt = select(ColumnInfoMySQL).where(
            and_(
                ColumnInfoMySQL.table_id == table_id,
                ColumnInfoMySQL.role.in_(['primary_key', 'foreign_key'])
            )
        )
        column_info_mysqls = await self.session.scalars(stmt)
        return [ColumnInfo.model_validate(column_info_mysql) for column_info_mysql in column_info_mysqls]
    
    async def get_table_info_by_id(self, table_id: str) -> TableInfo | None:
        #构造一个SQL查询语句，查询TableInfoMySQL表中id等于table_id的记录。
        stmt = select(TableInfoMySQL).where(TableInfoMySQL.id == table_id)
        #执行查询，并将结果存储在table_info_mysql变量中。
        table_info_mysql = await self.session.scalar(stmt)
        if table_info_mysql:
            #返回相应的TableInfo对象，如果没有找到对应的记录，则返回None。
            return TableInfo.model_validate(table_info_mysql)
        return None    

#操作业务数据库的DWDBRepository类，提供了获取表格列类型和列值的方法。
class DWDBRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str):
        sql = f"show columns from {table_name}"
        result = await self.session.execute(text(sql))
        #获取查找出来的每行的数据
        rows = result.fetchall()
        # product_id（列名）	varchar(20)（数据类型）
        # product_name（列名）	varchar(200)（数据类型）
        #定义了一个字典推导式，用于将查询结果中的列名和数据类型提取出来，并存储在一个字典中。
        column_types = {row[0]: row[1] for row in rows}
        #column_types = {列名：数据类型, ...}
        return column_types
    
#     column_types = {}
# for row in rows:
#     # 显式地将第0列作为键，第1列作为值存入字典
#     column_types[row[0]] = row[1]

# return column_types
    
    async def get_column_values(self, table_name: str, column_names: list[str], limit: int=10, offset: int=0) -> dict[str, list[Any]]:
        #distinct关键字用于返回唯一不同的值，避免重复数据。
        column_list = ",".join(column_names)
        sql = f"select distinct {column_list} from {table_name} limit {limit} offset {offset};"
        result = await self.session.execute(text(sql))
        rows = result.fetchall()
        column_values: dict[str, list[Any]] = {}
        # enumerate函数用于同时获取列索引和列名，以便将每列的值正确地存储在column_values字典中。
        for index, column_name in enumerate(column_names):
            #将行格式的数据(rows)转换为按列聚合的字典结构(column_values)，
            # 其中每个键是列名，对应的值是该列的所有数据列表。
            column_values[column_name] = [row[index] for row in rows]
        return column_values
    
    
# select distinct username, department from users;
# 假设查出来的 rows 数据如下（注意它是纯数字索引的元组）：
# rows = [
#     ('Alice', 'HR'),   # row[0]='Alice', row[1]='HR'
#     ('Bob', 'IT')      # row[0]='Bob',   row[1]='IT'
# ]

# 步骤 2：循环处理 (for index, column_name in enumerate(...))
# 第一次循环：
# index 是 0
# column_name 是 'username'
# 代码执行：column_values['username'] = [row[0] for row in rows]
# 结果：取出每行的第 0 个元素 -> ['Alice', 'Bob']

# 第二次循环：
# index 是 1
# column_name 是 'department'
# 代码执行：column_values['department'] = [row[1] for row in rows]
# 结果：取出每行的第 1 个元素 -> ['HR', 'IT']

    # 这里按列收集结果时，可能仍然出现重复值。
    # 原因是：
    # 1. 如果 SQL 没有做 distinct，那么原始数据里的重复值会直接保留下来；
    # 2. 即使 SQL 使用了 distinct，并且一次查询了多列，
    #    distinct 去重的也是“整行组合”，不是“单个列值”。
    #    例如 distinct(gender, city) 后，gender 这一列仍可能得到 ["男", "女", "女", "男"]。
    # 所以如果这个方法的目标是给每一列提供“候选枚举值”，通常还需要在这里再按列去重；
    # 如果目标是保留原始采样结果或频次特征，则不应该去重。

    async def get_db_info(self):
        """
        获取当前数据库的基本环境信息。
        用于向 LLM 提供数据库上下文，帮助生成兼容的 SQL 语句。
        """
        # 执行 SQL 查询获取数据库版本号
        version = await self.session.scalar(text("SELECT VERSION()"))
        # 从 SQLAlchemy 的连接绑定中获取方言名称（无需查询数据库）
        # dialect 表示数据库类型，例如：'mysql' / 'postgresql' / 'sqlite'
        # 用于告知 LLM 应生成哪种数据库方言的 SQL
        dialect = self.session.get_bind().dialect.name
        # 查询 information_schema 系统表，获取当前数据库的字符编码
        # DATABASE() 函数返回当前连接所使用的数据库名DW
        charset = await self.session.scalar(
            text(
                "SELECT DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = DATABASE()"
            )
        )
        # 将数据库信息以字典形式返回，供后续节点组装成自然语言描述传给 LLM
        return {"version": version, "dialect": dialect, "charset": charset}
    
    #校验SQL语句是否正确，是否符合规范。
    async def validate_sql(self, sql: str):
        await self.session.execute(text(f"explain {sql}"))

    #执行SQL语句，并返回结果。
    async def execute_sql(self, sql: str):
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]