from contextlib import asynccontextmanager
from pydantic import BaseModel, ConfigDict
from langgraph.graph import MessagesState, add_messages
from typing import Annotated, Any
from langchain.messages import AnyMessage
from typing import Optional, Literal
from clients.mysql import MySQLClient, client_registry
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from repositories.es import ESRepository
from repositories.mysql import MetaDBRepository, DWDBRepository
from conf.app_config import DEFAULT_DATASOURCE_ID
from dtos.meta import ColumnInfo, MetricInfo, ValueInfo

class WSAgentTableInfoState(BaseModel):
    id: str
    name: str
    role: Literal['dim', 'fact', 'bridge']
    description: str
    columns: list[ColumnInfo]

class WSAgentState(BaseModel):
    #Annotated的作用是为messages字段添加一个额外的验证器add_messages，
    # 这个验证器会在messages字段被赋值时被调用。
    #add_messages是langgraph的方法，用于将新的消息添加到现有的消息列表中。
    messages: Annotated[list[AnyMessage], add_messages]
    #多轮:最近几轮历史快照(每轮 question + sql + 结果前 N 行),由入口 query_graph 按 conversation_id
    #加载后注入。parse_query_intention 用它做指代消解,把当前追问改写成自包含问题;改写完即用完,
    #下游节点不读它(它们只读改写后的 messages[-1])。
    history: list[dict[str, Any]] | None = None
    #supervisor 路由已在同一次 LLM 调用里完成「分流+多轮改写+守门」(判定为数据查询,
    #messages[-1] 已是改写后的自包含问题)→ 意图节点据此短路,不再重复调用 LLM。
    #直接调用本图(evals 等)不设此标志,意图节点行为与从前完全一致。
    intent_pre_parsed: bool = False
    #内部子查询(归因/报告等上层 agent 发起的机器查询):只要结果数据,
    #interpret_result 据此跳过解读(省一次 LLM 调用);事件流由上层统一吞掉。
    internal_subquery: bool = False
    #Optional的作用是表示这个字段(should_continue)是可选的，
    # 可以为bool类型的值，也可以为None。
    should_continue: Optional[bool] = None
    #如果should_continue为false，
    # 给出用户指导性的查询语句(guide_queries)，帮助用户继续进行交互。
    guide_queries: Optional[list[str]] = None
    #关键词列表，存储从用户查询中提取的关键词，供后续节点使用
    keywords: list[str] | None = None
    #记录召回的列信息
    recalled_columns: list[ColumnInfo] | None = None
    #记录召回的指标信息
    recalled_metrics: list[MetricInfo] | None = None
    #记录召回的数值信息
    recalled_values: list[ValueInfo] | None = None
    #meta表信息
    table_infos: list[WSAgentTableInfoState] | None = None
    #meta指标信息
    metric_infos: list[MetricInfo] | None =  None
    #获取当前数据库的基本信息，用于后续 LLM 生成 SQL 时提供数据库环境上下文。
    db_info: str | None = None
    #当前日期时间上下文
    date_info: str | None = None
    #记录生成的 SQL 语句
    sql: str | None = None
    #记录错误信息
    error: Optional[str] = None
    #扇出检测结果：事实度量经一对多/多对多关系聚合到某维度时的重复计算警告(无风险则为 None)
    fanout_warning: Optional[str] = None
    #SQL 校正次数：每进一次 correct_sql 自增 1。graph 路由据此判断是否超过重试上限，
    #避免 SQL 永远修不好时在 validate_sql↔correct_sql 之间无限循环撞 recursion_limit。
    correct_attempts: int = 0
    # ── 执行结果(execute_sql 写入,translate_columns / interpret_result 读)──
    # execute_sql 跑完后的结果集(成功时是 list[dict],出错时为 None)
    sql_result: list[dict[str, Any]] | None = None
    # 结果是否被行数上限截断(>MAX_RESULT_ROWS),供解读环节提示用户"仅展示前 N 行"
    truncated: bool = False
    # interpret_result 节点产出的自然语言解读
    # (图表已是前端按需出图,data_shape/chart_config 字段随 chart_agent 独立迁走)
    interpretation: str | None = None
    # ── SQL 缓存(lookup_sql_cache 节点写入)─────────────────────────────
    # 本轮缓存键(归一化问题+数据源+库+版本的 sha256)。命中/未命中都会算出来,
    # 供命中判断 + 未命中时执行成功后写回缓存。
    cache_key: str | None = None
    # 是否命中缓存:True 时 sql 来自缓存(跳过生成,直接校验执行);
    # 也用于「命中的 SQL 校验失败 → 回退完整生成」的自愈路由。
    from_cache: bool = False
    # 算缓存键时读到的该数据源元数据版本,写回缓存时复用(避免再查一次库)
    meta_version: int | None = None


#WSAgentContext是一个Pydantic模型，定义了在整个图的执行过程中共享的上下文信息。
class WSAgentContext(BaseModel):
    column_qdrant_repo: ColumnQdrantRepository
    metric_qdrant_repo: MetricQdrantRepository
    es_repo: ESRepository
    # 不再持有"贯穿整条流"的长生命周期 DB 会话,改持有客户端(连接池工厂)。
    # 节点用下面的 meta_repo()/dw_repo() 上下文管理器,只在真正查库的那一小段开/关会话;
    # LLM 调用等待期间不占用任何 MySQL 连接 —— 同样的连接池能支撑高得多的并发。
    meta_db_client: MySQLClient
    # 当前会话查询的数据源 + 库(库不传则用该数据源的默认库)。
    # DW 连接由 dw_repo() 经 client_registry 按 datasource_id 动态解析。
    # 所有元数据查询/召回/DW 连接都按它作用域化。
    datasource_id: str = DEFAULT_DATASOURCE_ID
    database: str | None = None
    #全是自定义类，所以需要在模型配置中设置arbitrary_types_allowed=True，
    #允许使用任意类型的字段。
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @asynccontextmanager
    async def meta_repo(self):
        """开一个短生命周期的 meta 库会话 + repo(绑定本会话的 datasource_id),用完即还连接。"""
        async with self.meta_db_client.session() as session:
            yield MetaDBRepository(session, self.datasource_id)

    @asynccontextmanager
    async def dw_repo(self):
        """开一个短生命周期的 DW 会话 + repo。

        通过 client_registry 按 datasource_id/库拿对应连接池(从 meta 库的 datasource 表解析),
        每个库一个池、按需建。"""
        client = await client_registry.get_client(self.datasource_id, self.database)
        async with client.session() as session:
            yield DWDBRepository(session)


#实现到了每个步骤都要返回信息给前端
class WSStepInfo(BaseModel):
    #步骤的名字
    step: str
    #步骤的状态
    status: Literal['running', 'success', 'error']
    #可选的数据字段，可以用来携带额外的信息，比如错误信息、生成的SQL等。
    data: Any | None = None
    #可选的用户指导性查询语句，用于帮助用户继续进行交互。
    guide_queries: list[str] | None = None
    #执行节点带上「真正执行的那条 SQL」(主图=LIMIT 截断后的 state.sql,数据集=generated_sql),
    #供前端「查看 SQL」展示。专用字段,不复用 data(避免和结果数组/图表对象的类型分发冲突)。
    sql: str | None = None
    #整个的图，agent的状态
    finish: bool = False