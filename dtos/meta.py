from pydantic import BaseModel, ConfigDict
from typing import Any, Literal

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
    model_config = ConfigDict(from_attributes=True)


class ColumnMetric(BaseModel):
    column_id: str
    metric_id: str
    model_config = ConfigDict(from_attributes=True)


class MetricInfo(BaseModel):
    id: str
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]
    model_config = ConfigDict(from_attributes=True)


class TableInfo(BaseModel):
    id: str
    name: str
    role: Literal['dim', 'fact', 'bridge']
    description: str
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
    model_config = ConfigDict(from_attributes=True)