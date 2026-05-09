from elasticsearch import AsyncElasticsearch
from dtos.es import ValueInfo


class ESRepository:
    index_name = "value_info"
    index_mappings = {
        "properties": {
            # keyword类型：常用语结构化数据，比如ID、手机号、邮箱、性别，keyword类型的不会被分词
            "id": {"type": "keyword"},
            # IK提供了两种分词器，分别是
            #analyzer是写入数据时使用的分词器，search_analyzer是用来指定搜索时使用的分词器。
            # ik_smart：最少切分
            # ik_max_word：最细粒度划分
            "value": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_max_word"},
            "column_id": {"type": "keyword"}
        }
    }

    def __init__(self, client: AsyncElasticsearch):
        self.client = client

    async def clear_all(self):
        if (await self.client.indices.exists(index=self.index_name)):
            await self.client.indices.delete(index=self.index_name)
    #确定索引是否存在，如果不存在则创建索引。
    async def ensure_index(self):
        if not (await self.client.indices.exists(index=self.index_name)):
            await self.client.indices.create(index=self.index_name, mappings=self.index_mappings)
    #批量添加数据到ES中，使用bulk API，每次20条数据。
    async def add_documents(self, value_infos: list[ValueInfo]):
        batch_size: int = 20
        for index in range(0, len(value_infos), batch_size):
            batch_infos = value_infos[index: index+batch_size]
            operations = []
            for info in batch_infos:
                operations.append({"index": {"_index": self.index_name, "_id": info.id}})
                operations.append(info.model_dump())
    # {"index": {"_index": "value_info", "_id": "003"}},
    # {"id": "003", "value": "小米平板", "column_id": "col_1"},
            await self.client.bulk(operations=operations)