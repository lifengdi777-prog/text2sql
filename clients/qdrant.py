import asyncio
import random
from qdrant_client import AsyncQdrantClient, models
from conf.app_config import QdrantConfig, app_config

class QDrantClient:
    def __init__(self, qdrant_config: QdrantConfig):
        self.qdrant_config = qdrant_config
        self.qdrant_url = f"http://{self.qdrant_config.host}:{self.qdrant_config.port}"
        self.client = AsyncQdrantClient(url=self.qdrant_url)

    async def ensure_collection(self, collection_name: str):
        #检查集合是否存在，如果不存在则创建
        if not await self.client.collection_exists(collection_name):
            await self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=self.qdrant_config.embedding_size, 
                    distance=models.Distance.COSINE
                ),
            )

    async def close(self):
        await self.client.close()

qdrant_client = QDrantClient(app_config.qdrant)

if __name__ == '__main__':
    async def test():
        qdrant_client = QDrantClient(app_config.qdrant)
        await qdrant_client.ensure_collection("test_collection")
    asyncio.run(test())