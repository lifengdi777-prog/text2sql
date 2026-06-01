from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from conf.app_config import app_config

llm = ChatOpenAI(
    model=app_config.llm.model_name,
    api_key=SecretStr(app_config.llm.api_key),
    base_url=app_config.llm.base_url,
    temperature=0,
    timeout=150,
    max_retries=2
)

# 快模型:给"关键词扩展 / 表指标过滤 / 列名翻译"这类轻量结构化任务用。
# 这类活换成非推理的快模型(默认 deepseek-v4-flash)
# 复用同一 api_key / base_url,只换 model;timeout 调小(快任务不该长等)。
fast_llm = ChatOpenAI(
    model=app_config.llm.fast_model_name,
    api_key=SecretStr(app_config.llm.api_key),
    base_url=app_config.llm.base_url,
    temperature=0,
    timeout=60,
    max_retries=2
)