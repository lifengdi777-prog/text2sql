from typing import Any

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

    async def search(
        self,
        keyword: str,
        score_threshold: float = 0.6,
        limit: int = 5
    ) -> list[ValueInfo]:
        result = await self.client.search(
            index=self.index_name,
            query={
                "match": {
                    "value": keyword
                }
            },
            min_score=score_threshold,
            size=limit
        )
        return [ValueInfo.model_validate(hit['_source']) for hit in result['hits']['hits']]


class UploadESRepository:
    """上传数据集的值召回索引。

    共享索引 upload_value_info,所有数据集的值都进同一个索引,靠 dataset_id 字段隔离。
    多租户标准做法,千万级文档无压力(dataset_id 是 keyword,过滤几乎免费)。
    """
    index_name = "upload_value_info"
    index_mappings = {
        "properties": {
            "dataset_id": {"type": "keyword"},   # 多租户隔离用
            "sheet":      {"type": "keyword"},   # 哪个 sheet
            "col":        {"type": "keyword"},   # 哪一列
            # 主搜字段:中文分词 + ik_max_word(跟 DW 的 value_info 一致)
            "value":      {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_max_word"},
            # 原值精确返回(写 filter 时要用原始字符串)
            "value_raw":  {"type": "keyword"},
        }
    }

    def __init__(self, client: AsyncElasticsearch):
        self.client = client

    async def ensure_index(self) -> None:
        if not (await self.client.indices.exists(index=self.index_name)):
            await self.client.indices.create(index=self.index_name, mappings=self.index_mappings)

    async def index_dataset_values(self, dataset_id: int, docs: list[dict[str, Any]]) -> None:
        """批量索引一个数据集的所有 distinct values。

        docs 期望格式:[{sheet, col, value}, ...]
        会自动补 dataset_id / value_raw 字段,生成 doc_id。
        """
        if not docs:
            return
        ds_id = str(dataset_id)
        batch_size = 500
        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            operations = []
            for d in batch:
                value = str(d["value"])
                # doc_id 用 (dataset_id, sheet, col, value) 拼接保证幂等
                # 同一个 dataset 重传时同 value 不会重复索引
                doc_id = f"{ds_id}|{d['sheet']}|{d['col']}|{value}"[:512]
                operations.append({"index": {"_index": self.index_name, "_id": doc_id}})
                operations.append({
                    "dataset_id": ds_id,
                    "sheet": d["sheet"],
                    "col": d["col"],
                    "value": value,
                    "value_raw": value,
                })
            await self.client.bulk(operations=operations)

    async def search_values(
        self,
        dataset_id: int,
        keyword: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """在指定 dataset 内搜索匹配 keyword 的真实值。

        返回:[{sheet, col, value}, ...]
        """
        result = await self.client.search(
            index=self.index_name,
            query={
                "bool": {
                    "must": [
                        {"term": {"dataset_id": str(dataset_id)}},
                        {"match": {"value": keyword}},
                    ]
                }
            },
            size=limit,
        )
        return [
            {"sheet": h["_source"]["sheet"],
             "col":   h["_source"]["col"],
             "value": h["_source"]["value_raw"]}
            for h in result["hits"]["hits"]
        ]

    async def delete_dataset_values(self, dataset_id: int) -> None:
        """删除一个数据集在 ES 的所有值文档。删除数据集时调。"""
        await self.client.delete_by_query(
            index=self.index_name,
            query={"term": {"dataset_id": str(dataset_id)}},
            conflicts="proceed",
        )