import asyncio
from elasticsearch import AsyncElasticsearch
from conf.app_config import ESConfig, app_config


class ESClient:
    def __init__(self, es_config: ESConfig):
        self.es_config = es_config
        self.es_url = f"http://{self.es_config.host}:{self.es_config.port}"
        self.client = AsyncElasticsearch(self.es_url)

    async def close(self):
        await self.client.close()

es_client = ESClient(app_config.es)

if __name__ == '__main__':
    async def test():
        client = es_client.client
        index_name = "my-books"
        replica_settings = {"number_of_replicas": 0}

        try:
            if not await client.indices.exists(index=index_name):
                await client.indices.create(
                    index=index_name,
                    settings=replica_settings,
                    mappings={
                        "properties": {
                            "name": {
                                "type": "text"
                            },
                            "author": {
                                "type": "text"
                            },
                            "release_date": {
                                "type": "date",
                                "format": "yyyy-MM-dd"
                            },
                            "page_count": {
                                "type": "integer"
                            }
                        }
                    },
                )

            await client.indices.put_settings(
                index=index_name,
                settings=replica_settings,
            )

            health = await client.cluster.health(index=index_name)
            print(f"index={index_name}, status={health['status']}")

        finally:
            await es_client.close()

    asyncio.run(test())