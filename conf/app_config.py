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
    """应用自有的运营库:users / conversations / messages / upload_datasets /
    dataset_edit_*。database 字段是库名(默认 'wenshu')。
    """
    host: str
    port: int
    user: str
    password: str
    database: str = "wenshu"

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


# JWT 默认 secret(仅本地开发)。生产必须在 yaml 覆盖,否则 token 可被伪造;
# 启动时会检测是否仍是该默认值并告警(见 core/security.py)。
DEFAULT_JWT_SECRET = "wenshu-dev-secret-change-me-in-production"


class AuthConfig(BaseModel):
    """登录鉴权配置(JWT)。不配置则用下面的默认值(仅适合本地开发)。

    ⚠️ 生产环境务必在 app_config.yaml 里设一个随机的强 secret,
    否则 token 可被伪造。改了 secret 会让已签发的 token 全部失效。
    """
    secret: str = DEFAULT_JWT_SECRET
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7   # 7 天
    # 管理员 admin 的登录密码:以此为准,每次启动同步到 admin 账号(改这里→重启→admin 密码就变)。
    # 留空则首次随机生成并打印日志一次、之后不动。少于 6 位忽略。
    admin_password: str = ""

    model_config = ConfigDict(from_attributes=True)


class CORSConfig(BaseModel):
    """跨域(CORS)。allow_origins=['*'] 为放开(本地开发默认);
    生产应填明确的前端域名列表,如 ['https://wenshu.example.com']。

    注意:含 '*' 时浏览器禁止携带凭据,allow_credentials 会被强制为 False
    (见 main.py);需要带凭据的跨域,必须列明确域名。
    """
    allow_origins: list[str] = ["*"]

    model_config = ConfigDict(from_attributes=True)


class RateLimitConfig(BaseModel):
    """按用户限流,保护吃 LLM 配额的端点(问数/数据集问答/智能编辑/按需图表)。

    单进程内存实现(零依赖):多 worker 部署时各进程独立计数,
    实际上限 ≈ 配置值 × worker 数;需要跨进程精确限流再换 Redis。
    """
    enabled: bool = True
    # 单用户同时进行中的 LLM 管线数(SSE 流从开始到结束都算占用)
    max_concurrent: int = 2
    # 单用户每分钟最多发起的请求次数(滑动窗口)
    max_per_minute: int = 20

    model_config = ConfigDict(from_attributes=True)


class LangfuseConfig(BaseModel):
    """Langfuse 可观测(自托管/云)。enabled=false 或缺 key → 不追踪,全链路零侵入。"""
    enabled: bool = False
    public_key: str = ""
    secret_key: str = ""
    host: str = "http://localhost:3000"   # 自托管默认;Langfuse 云为 https://cloud.langfuse.com

    model_config = ConfigDict(from_attributes=True)


class AppConfig(BaseModel):
    logging: LoggingConfig
    db_meta: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    embedding_fallback: EmbeddingFallbackConfig
    es: ESConfig
    llm: LLMConfig
    # 上传功能用,不配置不影响原 DW 路径(用到时才校验)
    db_wenshu: UploadDBConfig | None = None
    # 对象存储,上传功能用;不配置不影响原 DW 路径(用到时才校验)
    s3: S3Config | None = None
    # 登录鉴权(JWT)。不配置则用 AuthConfig 的默认值(本地开发够用)
    auth: AuthConfig = AuthConfig()
    # 跨域。不配置则放开(['*']);生产应在 yaml 填明确的前端域名
    cors: CORSConfig = CORSConfig()
    # Langfuse 可观测。不配置/enabled=false 则不追踪(本地开发默认关)
    langfuse: LangfuseConfig = LangfuseConfig()
    # 按用户限流(LLM 端点)。不配置则用默认值(并发 2 / 每分钟 20)
    rate_limit: RateLimitConfig = RateLimitConfig()

config_path = Path(__file__).parent / "app_config.yaml"
#context就是从app_config.yaml文件中读取的配置数据。
context = OmegaConf.load(config_path)
#①model_validate的作用是将yaml文件的数据转换成AppConfig对象的属性
#②定义了一个全局的app_config变量，类型是AppConfig，
#并且通过调用AppConfig.model_validate(context)来初始化这个变量。
app_config: AppConfig = AppConfig.model_validate(context)

# 多数据源:现有这套手工维护的源,统一挂在这个固定 datasource_id 下。
# meta 表的作用域列、召回过滤、init_data 写入都用它当单源时的默认值;
# 多源接入后,新源用各自的 id。
DEFAULT_DATASOURCE_ID: str = "ds_default"