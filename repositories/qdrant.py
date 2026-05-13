from qdrant_client import AsyncQdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, Filter
from typing import Sequence
from dtos.qdrant import ColumnQdrantInfo, MetricQdrantInfo, BaseQdrantInfo
from abc import ABC
from dtos.meta import ColumnInfo, MetricInfo

class BaseQdrantRepository(ABC):
    collection_name: str

    #这里的AsyncQdrantClient是QDrantClient类中创建的异步客户端对象，用于与Qdrant数据库进行交互。
    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    #先判断集合是否存在，如果存在则删除集合中的所有数据。
    async def clear_all(self):
        if await self.client.collection_exists(self.collection_name):
            await self.client.delete(
                collection_name=self.collection_name,
                #①因为 Qdrant 的 delete() 方法不会默认删除全部数据，
                # 必须通过 points_selector 明确指定要删除哪些 point。
                #②当传入空的 Filter() 时，表示没有任何过滤条件，
                # 因此会匹配 collection 中的所有 point，从而删除所有数据。
                points_selector=Filter()
            )
 
    #先判断集合是否存在，如果不存在该集合则创建集合。
    async def ensure_collection(self):
        if not (await self.client.collection_exists(self.collection_name)):
            await self.client.create_collection(
                self.collection_name,
                vectors_config=VectorParams(
                    size=1024,
                    distance=Distance.COSINE
                )
            )
    #qdrant_infos: Sequence[BaseQdrantInfo] 表示传进来的是一组 Qdrant 数据对象。
    #这里用 Sequence[BaseQdrantInfo]，表示只要是序列就可以，比如：list、tuple 等等，而不局限于某一种具体的序列类型。
    async def upsert(self, qdrant_infos: Sequence[BaseQdrantInfo]):
        batch_size: int = 20
        #每次20条数据，直到遍历完整个 qdrant_infos。
        for index in range(0, len(qdrant_infos), batch_size):
            #每次截取20条数据，形成一个 batch_infos 列表。
            batch_infos = qdrant_infos[index: index + batch_size]
            #把业务对象转换成 Qdrant 的 PointStruct 对象，准备进行 upsert 操作。
            points = [
                PointStruct(id=info.id, vector=info.embeddings, payload=info.payload.model_dump())
                for info in batch_infos
            ]
            #写入 Qdrant 数据库中，进行 upsert 操作。
            await self.client.upsert(self.collection_name, points=points)

    #根据输入的 embedding 向 Qdrant 数据库中查询相似的点，返回它们的 payload。
    async def _search(
        self, 
        embedding: list[float], 
        score_threshold: float=0.6, 
        limit: int=5
    ) -> list:
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            score_threshold=score_threshold,
            limit=limit
        )
        return [point.payload for point in result.points]

#这里定义了两个子类，分别继承 BaseQdrantRepository。
#它们通过设置不同的 collection_name 来操作不同的 Qdrant 集合。
#同时它们重写了父类的 upsert 方法，但内部只是调用 super().upsert()，所以实际逻辑仍然复用父类的方法。
class ColumnQdrantRepository(BaseQdrantRepository):
    collection_name: str = "column_info"
    
    async def upsert(self, qdrant_infos: list[ColumnQdrantInfo]):
        await super().upsert(qdrant_infos)

    async def search(
        self,
        embedding: list[float], 
        score_threshold: float=0.6, 
        limit: int=5
    ) -> list[ColumnInfo]:
        #调用父类的 _search 方法，获取原始的 payload 列表。
        payloads = await super()._search(embedding, score_threshold, limit)
        return [ColumnInfo.model_validate(payload) for payload in payloads]

class MetricQdrantRepository(BaseQdrantRepository):
    collection_name: str = "metric_info"
    
    async def upsert(self, qdrant_infos: list[MetricQdrantInfo]):
        await super().upsert(qdrant_infos)

    async def search(
        self,
        embedding: list[float], 
        score_threshold: float=0.6, 
        limit: int=5
    ) -> list[MetricInfo]:
        #调用父类的 _search 方法，获取原始的 payload 列表。
        payloads = await super()._search(embedding, score_threshold, limit)
        return [MetricInfo.model_validate(payload) for payload in payloads]