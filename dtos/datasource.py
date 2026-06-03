from pydantic import BaseModel, ConfigDict


class DatasourceCreate(BaseModel):
    """注册数据源的入参,password 是明文(进库前由 repo 加密成 password_enc)。"""
    id: str
    name: str
    host: str
    port: int
    username: str
    password: str
    type: str = "mysql"
    default_database: str | None = None
    created_by: int | None = None


class DatasourceInfo(BaseModel):
    """对外展示用,**不含密码**(连列表/详情都不返回密文,更不返回明文)。"""
    id: str
    name: str
    type: str
    host: str
    port: int
    username: str
    default_database: str | None
    created_by: int | None
    status: str
    build_status: str | None = None
    table_count: int | None = None
    model_config = ConfigDict(from_attributes=True)
