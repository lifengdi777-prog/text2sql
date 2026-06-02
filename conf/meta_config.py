from pydantic import BaseModel, ConfigDict
from typing import Literal
from pathlib import Path
import json
#从meta_config.json文件中读取配置，并将其解析为MetaConfig对象。
# MetaConfig对象包含了表格和指标的配置信息。
class ColumnConfig(BaseModel):
    name: str
    role: Literal["primary_key", "foreign_key", "dimension", "measure"]
    description: str
    alias: list[str]
    sync: bool
    model_config = ConfigDict(from_attributes=True)


class TableConfig(BaseModel):
    name: str
    # dim=维表, fact=事实表, bridge=多对多桥接表(junction)
    role: Literal['dim', 'fact', 'bridge']
    description: str
    columns: list[ColumnConfig]
    model_config = ConfigDict(from_attributes=True)


class MetricConfig(BaseModel):
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]
    model_config = ConfigDict(from_attributes=True)


class MetaConfig(BaseModel):
    tables: list[TableConfig]
    metrics: list[MetricConfig]
    model_config = ConfigDict(from_attributes=True)


if __name__ == "__main__":
    # 读取文件
    config_path = Path(__file__).parent / "meta_config.json"
    fp = open(config_path, "r", encoding="utf-8")
    config_dict = json.load(fp)
    meta_config = MetaConfig.model_validate(config_dict)
    fp.close()
    print(meta_config)