from pydantic import BaseModel, ConfigDict
from omegaconf import OmegaConf
from pathlib import Path

class FileLocationConfig(BaseModel):
    enable: bool       
    level: str        
    path: str          
    rotation: str   
    retention: str 

    model_config = ConfigDict(from_attributes=True)

class ConsoleLoggingConfig(BaseModel):
    enable: bool       
    level: str        

    model_config = ConfigDict(from_attributes=True)

class LoggingConfig(BaseModel):
    file:  FileLocationConfig
    console: ConsoleLoggingConfig
    
    model_config = ConfigDict(from_attributes=True)

class DBConfig(BaseModel):
    host: str
    port: int
    user: str
    password: str
    database: str

    model_config = ConfigDict(from_attributes=True)


class UploadDBConfig(BaseModel):
    """上传专用 DB:database 字段是 catalog 库的名字(默认 'upload')。

    动态建 up_xxx 库时不用 database 字段,直接连服务端 root。
    """
    host: str
    port: int
    user: str
    password: str
    database: str = "upload"

    model_config = ConfigDict(from_attributes=True)

class QdrantConfig(BaseModel):
    host: str
    port: int
    embedding_size: int
    model_config = ConfigDict(from_attributes=True)

class EmbeddingConfig(BaseModel):
    host: str
    port: int
    enable: bool
    # embedding 服务单次批量上限，超过会分批并行调用。不同服务上限不同
    # （DashScope=10，OpenAI=2048，本地模型一般无限）。默认 10 兼容旧配置。
    max_batch_size: int = 10
    model_config = ConfigDict(from_attributes=True)

class EmbeddingFallbackConfig(BaseModel):
    base_url: str
    api_key: str
    model_name: str
    max_batch_size: int = 10
    model_config = ConfigDict(from_attributes=True)


class ESConfig(BaseModel):
    host: str
    port: int
    index_name: str

    model_config = ConfigDict(from_attributes=True)

class LLMConfig(BaseModel):
    model_name: str
    api_key: str
    base_url: str
    # 表头检测等"简单结构化小任务"用的快模型(独立请求,不跟问数抢同一额度/限流桶)。
    # 不配则用 model_name;默认走 deepseek-v4-flash。
    fast_model_name: str = "deepseek-v4-flash"

    model_config = ConfigDict(from_attributes=True)


class S3Config(BaseModel):
    """对象存储(MinIO / 兼容 S3)。存用户上传的原始 Excel + 各 sheet 的 parquet。

    region 对 MinIO 无意义,但 boto3 需要一个值;默认 us-east-1 即可。
    """
    endpoint_url: str                 # 例:http://localhost:9000
    access_key: str
    secret_key: str
    bucket: str = "wenshu-datasets"
    region: str = "us-east-1"

    model_config = ConfigDict(from_attributes=True)


class AuthConfig(BaseModel):
    """登录鉴权配置(JWT)。不配置则用下面的默认值(仅适合本地开发)。

    ⚠️ 生产环境务必在 app_config.yaml 里设一个随机的强 secret,
    否则 token 可被伪造。改了 secret 会让已签发的 token 全部失效。
    """
    secret: str = "wenshu-dev-secret-change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7   # 7 天

    model_config = ConfigDict(from_attributes=True)

class AppConfig(BaseModel):
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    embedding_fallback: EmbeddingFallbackConfig
    es: ESConfig
    llm: LLMConfig
    # 上传功能用,不配置不影响原 DW 路径(用到时才校验)
    db_upload: UploadDBConfig | None = None
    # 对象存储,上传功能用;不配置不影响原 DW 路径(用到时才校验)
    s3: S3Config | None = None
    # 登录鉴权(JWT)。不配置则用 AuthConfig 的默认值(本地开发够用)
    auth: AuthConfig = AuthConfig()

config_path = Path(__file__).parent / "app_config.yaml"
#context就是从app_config.yaml文件中读取的配置数据。
context = OmegaConf.load(config_path)
#①model_validate的作用是将yaml文件的数据转换成AppConfig对象的属性
#②定义了一个全局的app_config变量，类型是AppConfig，
#并且通过调用AppConfig.model_validate(context)来初始化这个变量。
app_config: AppConfig = AppConfig.model_validate(context)