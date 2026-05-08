from langchain_openai import OpenAIEmbeddings
from conf.app_config import EmbeddingConfig, app_config
from pydantic import SecretStr

class EmbeddingClient:
    def __init__(self, embedding_config: EmbeddingConfig):
        self.embedding_cofig = embedding_config
        self.embedding_url = f"http://{embedding_config.host}:{embedding_config.port}"
        #定义了一个OpenAIEmbeddings对象，并将其赋值给self.client属性。这个对象用于生成文本的嵌入向量。
        self.client = OpenAIEmbeddings(
            base_url=self.embedding_url,
            api_key=SecretStr("..."),
            model="..."
        )

embedding_client = EmbeddingClient(app_config.embedding)


if __name__ == "__main__":
    import asyncio
    async def test():
        vector = await embedding_client.client.aembed_query("你是谁？")
        print(vector[:10])
    asyncio.run(test())