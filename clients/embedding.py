import asyncio

from langchain_openai import OpenAIEmbeddings
from conf.app_config import EmbeddingConfig, app_config
from pydantic import SecretStr


class EmbeddingClient:
    def __init__(self):
        embedding_config = app_config.embedding
        if embedding_config.enable:
            embedding_url = f"http://{embedding_config.host}:{embedding_config.port}"
            self.client = OpenAIEmbeddings(
                base_url=embedding_url,
                api_key=SecretStr("..."),
                model="..."
            )
            # 单次批量上限取生效配置的值
            self.max_batch = embedding_config.max_batch_size
        else:
            embedding_fallback_config = app_config.embedding_fallback
            self.client = OpenAIEmbeddings(
                base_url=embedding_fallback_config.base_url,
                api_key=SecretStr(embedding_fallback_config.api_key),
                model=embedding_fallback_config.model_name,
                tiktoken_enabled=False,
                check_embedding_ctx_length=False
            )
            self.max_batch = embedding_fallback_config.max_batch_size

    async def aembed_documents_batched(self, texts: list[str]) -> list[list[float]]:
        """把 texts 按 self.max_batch 切片，每片并行 embed，再按原顺序拼回。

        绕开服务端的单次批量上限，同时保留并行带来的延迟收益。
        """
        if not texts:
            return []
        batches = [
            texts[i: i + self.max_batch]
            for i in range(0, len(texts), self.max_batch)
        ]
        batch_results = await asyncio.gather(*[
            self.client.aembed_documents(batch) for batch in batches
        ])
        # 展平：保持与 texts 一致的顺序
        return [embedding for batch in batch_results for embedding in batch]


embedding_client = EmbeddingClient()


if __name__ == "__main__":
    import asyncio
    async def test():
        vector = await embedding_client.client.aembed_query("你好吗？")
        print(vector[:10])
    asyncio.run(test())