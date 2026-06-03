from pydantic import BaseModel

from conf.app_config import DEFAULT_DATASOURCE_ID


class ValueInfo(BaseModel):
    id: str
    value: str
    column_id: str
    # 默认 ds_default:让旧文档/旧构造仍能过校验,单源不变;多源时显式传值。
    datasource_id: str = DEFAULT_DATASOURCE_ID
