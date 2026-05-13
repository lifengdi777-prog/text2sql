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

class QdrantConfig(BaseModel):
    host: str
    port: int
    embedding_size: int
    model_config = ConfigDict(from_attributes=True)

class EmbeddingConfig(BaseModel):
    host: str
    port: int
    enable: bool
    model_config = ConfigDict(from_attributes=True)

class EmbeddingFallbackConfig(BaseModel):
    base_url: str
    api_key: str
    model_name: str
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

config_path = Path(__file__).parent / "app_config.yaml"
#context就是从app_config.yaml文件中读取的配置数据。
context = OmegaConf.load(config_path)
#①model_validate的作用是将yaml文件的数据转换成AppConfig对象的属性
#②定义了一个全局的app_config变量，类型是AppConfig，
#并且通过调用AppConfig.model_validate(context)来初始化这个变量。
app_config: AppConfig = AppConfig.model_validate(context)