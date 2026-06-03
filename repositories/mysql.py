from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from models.meta import ColumnInfoMySQL, MetricInfoMySQL, TableInfoMySQL, ColumnMetricMySQL, DataRelationshipMySQL
from dtos.meta import ColumnInfo, TableInfo, MetricInfo, ColumnMetric, DataRelationship
from conf.app_config import DEFAULT_DATASOURCE_ID
from sqlalchemy import text, and_, or_
from typing import Any
from decimal import Decimal


def _to_jsonable(value: Any) -> Any:
    """把数据库取出的值规整成 JSON 可序列化类型。

    DECIMAL 列(如 production_hours)会被驱动读成 Decimal，而 Decimal 无法被
    json.dumps 序列化，会让写入 JSON 列(examples)、Qdrant payload、ES 文档时报错。
    这里统一转成 float；其它类型原样返回。
    """
    if isinstance(value, Decimal):
        return float(value)
    return value

# MetaDBRepository类用于操作元数据库中的表格和指标信息。
# datasource_id 绑定在实例上:meta_repo() 按当前会话的数据源构造,所有查询/清理自动限定本源,
# 各业务节点(merge/join/fanout)无需逐个方法传参。
class MetaDBRepository:
    def __init__(self, session: AsyncSession, datasource_id: str = DEFAULT_DATASOURCE_ID):
        self.session = session
        self.datasource_id = datasource_id

    # 只清本数据源的元数据,不再清全表(否则注册第 2 个源会清掉第 1 个)。
    # include_relationships=False:编辑保存时保留 data_relationship(关系由人单独管,不随重物化重建)。
    async def clear_all(self, include_relationships: bool = True):
        models = [ColumnMetricMySQL, MetricInfoMySQL, ColumnInfoMySQL, TableInfoMySQL]
        if include_relationships:
            models.append(DataRelationshipMySQL)
        for model in models:
            await self.session.execute(delete(model).where(model.datasource_id == self.datasource_id))

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

    # 写入表关系(外键边),供连接路径补全/扇出检测使用。
    async def add_relationships(self, relationships: list[DataRelationship]):
        self.session.add_all([DataRelationshipMySQL(**rel.model_dump()) for rel in relationships])

    # 人工编辑「表关系」专用:整表替换本数据源的关系(先删本源旧边,再写新边)。
    # 只动 data_relationship,不碰别的表、不重建索引 —— 关系即时生效。
    async def replace_relationships(self, relationships: list[DataRelationship]):
        await self.session.execute(
            delete(DataRelationshipMySQL).where(DataRelationshipMySQL.datasource_id == self.datasource_id)
        )
        await self.add_relationships(relationships)

    # 列出本数据源的全部表(给意图解析节点拼"当前数据库领域"上下文用)。
    async def get_all_tables(self) -> list[TableInfo]:
        stmt = select(TableInfoMySQL).where(TableInfoMySQL.datasource_id == self.datasource_id)
        rows = await self.session.scalars(stmt)
        return [TableInfo.model_validate(row) for row in rows]

    # 列出本数据源的全部指标(同上)。
    async def get_all_metrics(self) -> list[MetricInfo]:
        stmt = select(MetricInfoMySQL).where(MetricInfoMySQL.datasource_id == self.datasource_id)
        rows = await self.session.scalars(stmt)
        return [MetricInfo.model_validate(row) for row in rows]

    # 列出本数据源的全部列(给元数据编辑页加载;按表分组在调用方做)。
    async def get_all_columns(self) -> list[ColumnInfo]:
        stmt = select(ColumnInfoMySQL).where(ColumnInfoMySQL.datasource_id == self.datasource_id)
        rows = await self.session.scalars(stmt)
        return [ColumnInfo.model_validate(row) for row in rows]

    # 读取本数据源的全部表关系(边集很小,直接全量取出，在内存里建图跑 BFS)。
    async def get_relationships(self) -> list[DataRelationship]:
        stmt = select(DataRelationshipMySQL).where(DataRelationshipMySQL.datasource_id == self.datasource_id)
        rows = await self.session.scalars(stmt)
        return [DataRelationship.model_validate(row) for row in rows]

    async def get_column_info_by_id(self, column_id: str) -> ColumnInfo | None:
        # id 只在单库内唯一,必须连 datasource_id 一起查。
        stmt = select(ColumnInfoMySQL).where(
            and_(ColumnInfoMySQL.datasource_id == self.datasource_id, ColumnInfoMySQL.id == column_id)
        )
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
                ColumnInfoMySQL.datasource_id == self.datasource_id,
                ColumnInfoMySQL.table_id == table_id,
                ColumnInfoMySQL.role.in_(['primary_key', 'foreign_key'])
            )
        )
        column_info_mysqls = await self.session.scalars(stmt)
        return [ColumnInfo.model_validate(column_info_mysql) for column_info_mysql in column_info_mysqls]

    async def get_table_info_by_id(self, table_id: str) -> TableInfo | None:
        #构造一个SQL查询语句，查询TableInfoMySQL表中id等于table_id的记录。
        stmt = select(TableInfoMySQL).where(
            and_(TableInfoMySQL.datasource_id == self.datasource_id, TableInfoMySQL.id == table_id)
        )
        #执行查询，并将结果存储在table_info_mysql变量中。
        table_info_mysql = await self.session.scalar(stmt)
        if table_info_mysql:
            #返回相应的TableInfo对象，如果没有找到对应的记录，则返回None。
            return TableInfo.model_validate(table_info_mysql)
        return None

# 幂等迁移:给已存在的 column_info 表补 sync 列(人工审核要可见可改),并按启发式回填存量行。
# column_info 由 init_data 重建,但 UI 物化的源 meta 也在这张表里,故启动时单独补列更安全。
async def ensure_meta_columns(engine) -> None:
    free_text = "('text','tinytext','mediumtext','longtext','blob','tinyblob','mediumblob','longblob','json')"
    async with engine.begin() as conn:
        existing = set((await conn.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='column_info'"
        ))).scalars().all())
        if not existing or "sync" in existing:
            return
        await conn.execute(text("ALTER TABLE column_info ADD COLUMN sync TINYINT(1) NULL"))
        # 回填:维度 + 非自由文本 → 1,与 introspect 的 sync 默认启发式一致;其余 0
        await conn.execute(text(
            f"UPDATE column_info SET sync = CASE WHEN role='dimension' "
            f"AND LOWER(type) NOT IN {free_text} THEN 1 ELSE 0 END WHERE sync IS NULL"
        ))


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
            column_values[column_name] = [_to_jsonable(row[index]) for row in rows]
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

    async def get_foreign_keys(self) -> list[dict[str, str]]:
        """读取当前 DW 库声明的所有外键关系(from 子表/多 → to 父表/一)。
        直接取自 information_schema,无需人工维护;dw.sql 里的 FOREIGN KEY 约束就是来源。"""
        sql = """
            SELECT TABLE_NAME            AS from_table,
                   COLUMN_NAME           AS from_column,
                   REFERENCED_TABLE_NAME AS to_table,
                   REFERENCED_COLUMN_NAME AS to_column
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
        """
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]

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