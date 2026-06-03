from pydantic import BaseModel, ConfigDict
from typing import Any, Literal

from conf.app_config import DEFAULT_DATASOURCE_ID

# datasource_id 给默认值,是为了让"还没传作用域"的旧构造/旧 Qdrant payload(没有该字段)
# 仍能通过校验,落在 ds_default 上 —— 单源行为不变,多源时再显式传值。
#这些类定义了数据模型，用于表示数据库中的表格、列、指标等信息。这些模型可以用于数据验证、序列化和反序列化等操作。
class ColumnInfo(BaseModel):
    id: str
    name: str
    type: str
    role: Literal['primary_key', 'foreign_key', 'dimension', 'measure']
    examples: list[Any]
    description: str
    alias: list[str]
    table_id: str
    # 值是否灌入 ES(人工可改);旧 Qdrant payload 没这字段时默认 False,不影响
    sync: bool = False
    datasource_id: str = DEFAULT_DATASOURCE_ID
    model_config = ConfigDict(from_attributes=True)


class ColumnMetric(BaseModel):
    column_id: str
    metric_id: str
    datasource_id: str = DEFAULT_DATASOURCE_ID
    model_config = ConfigDict(from_attributes=True)


class MetricInfo(BaseModel):
    id: str
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]
    datasource_id: str = DEFAULT_DATASOURCE_ID
    model_config = ConfigDict(from_attributes=True)


class TableInfo(BaseModel):
    id: str
    name: str
    role: Literal['dim', 'fact', 'bridge']
    description: str
    datasource_id: str = DEFAULT_DATASOURCE_ID
    model_config = ConfigDict(from_attributes=True)


class ValueInfo(BaseModel):
    id: str
    value: str
    column_id: str
    model_config = ConfigDict(from_attributes=True)


class DataRelationship(BaseModel):
    # 一条外键关系：from_table(多) 的 from_column 引用 to_table(一) 的 to_column。
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    description: str | None = None
    datasource_id: str = DEFAULT_DATASOURCE_ID
    model_config = ConfigDict(from_attributes=True)