# wenshu

`wenshu` 是一个面向 Text2SQL 场景的元数据构建项目。它的核心目标不是直接对外提供查询接口，而是先把业务数仓中的表、字段、指标和值域整理成多种可检索的索引，供后续的自然语言理解、字段召回、指标召回和枚举值匹配使用。

当前代码的主流程集中在 `scripts/init_data.py`：

1. 从 DW 业务库读取表结构和样例值。
2. 把表、字段、指标写入 Meta 元数据库。
3. 把字段和指标转成向量写入 Qdrant。
4. 把配置允许同步的枚举值写入 Elasticsearch。

## 项目架构

项目采用比较清晰的分层结构，职责大致如下：

### 1. 配置层 `conf/`

- `conf/app_config.py`：定义应用运行配置模型，并从 `conf/app_config.yaml` 加载 MySQL、Qdrant、Embedding、ES、LLM、日志等基础配置。
- `conf/meta_config.py`：定义元数据配置模型，对应 `conf/meta_config.json` 中的表、字段、指标描述。
- `conf/meta_config.json`：是业务语义配置中心，决定有哪些表、字段、指标会被同步，以及哪些字段的取值需要进入 ES 建索引。

这一层的作用是把“外部环境配置”和“业务语义配置”分开：

- `app_config.yaml` 解决服务连到哪里。
- `meta_config.json` 解决系统应该理解什么业务对象。

### 2. 客户端层 `clients/`

客户端层负责初始化外部系统连接，提供统一入口，不直接承载业务语义。

- `clients/mysql.py`：创建 DW 库和 Meta 库的 SQLAlchemy 异步连接。
- `clients/qdrant.py`：封装 `AsyncQdrantClient`。
- `clients/es.py`：封装 `AsyncElasticsearch`。
- `clients/embedding.py`：封装 Embedding 模型调用，支持本地 embedding 服务和远端 fallback 模型。

这一层可以理解成“基础设施适配器”。上层代码只管拿 `client` 用，不用关心连接细节。

### 3. DTO 层 `dtos/`

DTO 层负责在不同层之间传递结构化数据。

- `dtos/meta.py`：定义 `TableInfo`、`ColumnInfo`、`MetricInfo`、`ColumnMetric` 等对象。
- `dtos/qdrant.py`：定义写入 Qdrant 的点结构，核心是 `embeddings + payload`。
- `dtos/es.py`：定义写入 ES 的 `ValueInfo` 文档。

这一层的作用是把“数据库行”“配置项”“搜索索引文档”统一成明确的数据结构，避免直接在代码里拼字典。

### 4. 模型层 `models/`

- `models/__init__.py`：定义 SQLAlchemy Base。
- `models/meta.py`：定义 Meta 元数据库中的 ORM 表模型，包括：
	- `table_info`
	- `column_info`
	- `metric_info`
	- `column_metric`

这一层只服务于 Meta MySQL 数据库，是持久化层的表结构定义。

### 5. 仓储层 `repositories/`

仓储层把“怎么操作某个存储”封装成方法，业务流程只调用仓储，不直接操作底层 SDK。

- `repositories/mysql.py`
	- `MetaDBRepository`：向 Meta 库写入表、字段、指标和字段指标关系。
	- `DWDBRepository`：从 DW 库读取字段类型和字段值。
- `repositories/qdrant.py`
	- `ColumnQdrantRepository`：操作字段向量集合。
	- `MetricQdrantRepository`：操作指标向量集合。
- `repositories/es.py`
	- `ESRepository`：操作枚举值索引，负责清空索引、建索引和批量写入文档。

这一层解决的是“面向存储的操作语义”，例如“清空集合”“批量 upsert”“取字段值”。

### 6. 脚本层 `scripts/`

- `scripts/init_data.py`：当前项目最核心的脚本，负责初始化整套元数据和检索索引。

它实际上承担了“数据同步编排器”的角色，负责把配置层、客户端层、仓储层和 DTO 层串起来。

### 7. 基础设施层

- `core/log.py`：统一配置 `loguru` 日志。
- `docker/docker-compose.yaml`：启动 MySQL、Elasticsearch、Kibana、Qdrant、本地 embedding 服务。
- `main.py`：目前是一个很轻量的入口，主要用于日志和 LLM 调用试验，不是主业务入口。

## 目录职责速览

```text
wenshu/
├─ main.py                 # 轻量入口，当前主要用于测试/示例
├─ conf/                   # 基础配置与业务元数据配置
├─ clients/                # 外部服务客户端封装
├─ dtos/                   # 层间传输对象
├─ models/                 # Meta 库 ORM 模型
├─ repositories/           # 各存储的仓储封装
├─ scripts/                # 数据初始化与同步脚本
├─ docker/                 # 本地依赖服务编排
├─ embedding/              # 本地 embedding 模型目录
└─ core/                   # 日志等通用能力
```

## 核心逻辑如何联动

下面是当前最重要的一条调用链。

### 总入口

`scripts/init_data.py` 中的 `main()` 负责按顺序执行四件事：

1. `sync_dw_to_meta_db()`
2. `sync_meta_column_to_qdrant(column_infos)`
3. `sync_meta_metric_to_qdrant(metric_infos)`
4. `sync_dw_value_to_es(column_infos)`

最后统一关闭 ES 和 Qdrant 连接。

### 步骤 1：DW -> Meta MySQL

`sync_dw_to_meta_db()` 是整个链路的起点。

它做了两类同步：

#### 1. 同步表和字段

- 从 `meta_config.json` 读取要处理的表和字段。
- 用 `DWDBRepository.get_column_types()` 查询 DW 表字段类型。
- 用 `DWDBRepository.get_column_values()` 读取字段样例值。
- 组装成 `TableInfo` 和 `ColumnInfo` DTO。
- 调用 `MetaDBRepository.add_table_infos()` 和 `MetaDBRepository.add_column_infos()` 写入 Meta 库。

这里形成的是“结构化元数据底座”。后续不再直接依赖原始 DW 表结构，而是先依赖这份标准化后的 Meta 信息。

#### 2. 同步指标和字段-指标关系

- 从 `meta_config.json` 的 `metrics` 读取指标定义。
- 组装 `MetricInfo`。
- 把 `relevant_columns` 展开成多条 `ColumnMetric` 关系。
- 调用 `MetaDBRepository.add_metric_infos()` 和 `MetaDBRepository.add_column_metrics()` 写入 Meta 库。

这一步把“指标”和“相关字段”的语义关系显式存下来，后续可以用于 SQL 生成或指标解释。

### 步骤 2：Meta Column -> Qdrant

`sync_meta_column_to_qdrant(column_infos)` 负责把字段元数据转成向量检索索引。

调用链是：

1. 遍历 `column_infos`
2. 把 `字段名 + 描述 + 别名` 组成文本列表
3. 调用 `embedding_client.client.aembed_documents()` 生成多条 embedding
4. 每条 embedding 组装成一个 `ColumnQdrantInfo`
5. 用 `ColumnQdrantRepository` 清空集合、建集合、批量 upsert

当前实现的特点是：一个字段会因为“名称、描述、别名”被拆成多条向量点，而不是一字段一条向量。这样做有利于召回多个表达，但会增加 point 数量。

### 步骤 3：Meta Metric -> Qdrant

`sync_meta_metric_to_qdrant(metric_infos)` 和字段同步逻辑一致，只是对象变成指标。

调用链是：

1. 遍历 `metric_infos`
2. 把 `指标名 + 描述 + 别名` 组成文本列表
3. 生成 embedding
4. 组装成 `MetricQdrantInfo`
5. 用 `MetricQdrantRepository` 写入 `metric_info` 集合

因此，Qdrant 当前承担的是“语义召回层”，重点解决自然语言如何映射到字段和指标。

### 步骤 4：DW 枚举值 -> Elasticsearch

`sync_dw_value_to_es(column_infos)` 的职责和 Qdrant 不同。它不做语义向量，而是做字段值检索。

调用链是：

1. 遍历 `meta_config.tables`
2. 检查每个字段的 `sync` 是否为 `true`
3. 对允许同步的字段调用 `DWDBRepository.get_column_values()` 查询 DW 中的值
4. 把每个值组装成 `ValueInfo`
5. 用 `ESRepository` 清索引、建索引、批量写入

这里的用途主要是：

- 让自然语言中的实体值能被检索到，例如地区、品牌、客户名、品类。
- 帮助后续把“查询条件里的字面值”映射回具体字段。

当前 `get_column_values()` 使用 `select distinct ...`，因此 ES 条数更接近“候选值去重后总数”，而不是源表总行数。

## 方法之间的协作关系

可以把当前系统理解成 3 层联动。

### 第一层：定义业务语义

`meta_config.json` 提供：

- 表是什么
- 字段是什么
- 指标是什么
- 哪些字段值需要进入 ES

这是业务语义的来源。

### 第二层：标准化存储

`sync_dw_to_meta_db()` 把 DW 和配置融合后，落到 Meta MySQL：

- 表
- 字段
- 指标
- 字段和指标的关系

这是系统内部的标准元数据中心。

### 第三层：构建检索能力

- Qdrant 面向字段/指标语义召回。
- ES 面向枚举值和字面值检索。

二者结合后，后续 Text2SQL 流程就可以分别回答两个问题：

- 用户说的“销售额、客单价、大区”对应哪个字段或指标？
- 用户说的“华东、苹果、钻石会员”对应哪个字段值？

## 当前实现的角色划分

从当前代码看，这个项目更像“Text2SQL 的索引准备层”，而不是完整的在线问答服务。已实现的重点是离线建模和索引准备：

- Meta MySQL：元数据事实表
- Qdrant：字段/指标语义向量库
- Elasticsearch：枚举值索引
- Embedding / LLM：模型能力入口

`main.py` 中虽然已经有 LLM 的试验代码，但真正的在线问答编排、提示词、检索融合、SQL 生成与执行闭环，目前还没有在仓库里形成完整主链路。

## 数据流总结

可以把全流程概括成下面这条链路：

```text
meta_config.json
		+
DW MySQL
		|
		v
sync_dw_to_meta_db()
		|
		+--> Meta MySQL(table_info / column_info / metric_info / column_metric)
		|
		+--> sync_meta_column_to_qdrant() --> Qdrant.column_info
		|
		+--> sync_meta_metric_to_qdrant() --> Qdrant.metric_info
		|
		+--> sync_dw_value_to_es() --> Elasticsearch.value_info
```

## 运行方式

### 1. 启动依赖服务

项目依赖 MySQL、Elasticsearch、Kibana、Qdrant 和 embedding 服务。可以用 Docker Compose 启动：

```bash
docker compose -f docker/docker-compose.yaml up -d
```

### 2. 安装 Python 依赖

项目使用 Python `>=3.13,<3.14`，依赖定义在 `pyproject.toml`。

如果你使用 `uv`：

```bash
uv sync
```

如果你使用 `pip`：

```bash
pip install -e .
```

### 3. 检查配置

重点检查：

- `conf/app_config.yaml`
- `conf/meta_config.json`

建议把外部服务地址、账号和密钥改成你自己的环境值，不要直接依赖仓库中的示例配置。

### 4. 初始化元数据与索引

```bash
python scripts/init_data.py
```

执行完成后，理论上会得到：

- Meta 库中的元数据表
- Qdrant 中的 `column_info`、`metric_info` 集合
- ES 中的 `value_info` 索引

## 关键文件索引

- `scripts/init_data.py`：主同步流程
- `conf/meta_config.json`：业务元数据配置
- `conf/app_config.yaml`：运行时外部依赖配置
- `clients/mysql.py`：MySQL 客户端
- `clients/qdrant.py`：Qdrant 客户端
- `clients/es.py`：ES 客户端
- `clients/embedding.py`：Embedding 客户端
- `repositories/mysql.py`：DW/Meta MySQL 仓储
- `repositories/qdrant.py`：Qdrant 仓储
- `repositories/es.py`：ES 仓储
- `models/meta.py`：Meta 库 ORM 表定义
- `dtos/meta.py`：核心元数据 DTO

## 适合继续扩展的方向

如果要把它补成完整的 Text2SQL 系统，下一步通常会补这些模块：

1. 查询理解入口：把用户问题拆成指标、维度、过滤值、时间范围。
2. 检索编排层：联合 Qdrant 和 ES 做召回与重排。
3. SQL 生成层：把召回到的元数据拼成可执行 SQL。
4. SQL 执行与结果解释层：查询 DW 并返回自然语言答案。

当前仓库已经把这些能力中最重要的“元数据底座”和“检索索引底座”搭好了。
