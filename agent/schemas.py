from pydantic import BaseModel, ConfigDict
from langgraph.graph import MessagesState, add_messages
from typing import Annotated, Any
from langchain.messages import AnyMessage
from typing import Optional, Literal
from repositories.qdrant import ColumnQdrantRepository, MetricQdrantRepository
from repositories.es import ESRepository
from repositories.mysql import MetaDBRepository, DWDBRepository
from dtos.meta import ColumnInfo, MetricInfo, ValueInfo
# Chart Agent 子图相关 schema(WSAgentState 里要嵌)
from agent.chart_agent.schemas import DataShape, EChartsSpec

class WSAgentTableInfoState(BaseModel):
    id: str
    name: str
    role: Literal['dim', 'fact']
    description: str
    columns: list[ColumnInfo]

class WSAgentState(BaseModel):
    #Annotated的作用是为messages字段添加一个额外的验证器add_messages，
    # 这个验证器会在messages字段被赋值时被调用。
    #add_messages是langgraph的方法，用于将新的消息添加到现有的消息列表中。
    messages: Annotated[list[AnyMessage], add_messages]
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
    # ── 以下字段由 chart_agent 子图使用 ───────────────────────────────
    # execute_sql 跑完后的结果集(成功时是 list[dict],出错时为 None)
    sql_result: list[dict[str, Any]] | None = None
    # 结果是否被行数上限截断(>MAX_RESULT_ROWS),供解读环节提示用户"仅展示前 N 行"
    truncated: bool = False
    # analyzer 算出的数据形状摘要
    data_shape: DataShape | None = None
    # LLM 直出的 ECharts spec(generator / corrector 写入)
    chart_spec: EChartsSpec | None = None
    # validator 写入的校验问题列表,非空时触发 corrector
    chart_issues: list[str] | None = None
    # corrector 重试次数,超 MAX_RETRY 则降级 table
    chart_retry_count: int = 0
    # chart_agent 内部记的临时错误信息(generator / corrector 异常时填)
    chart_error: str | None = None
    # 最终 ECharts 配置(前端拿这个 setOption)
    chart_config: dict[str, Any] | None = None
    # interpret_result 节点产出的自然语言解读(与图表并行生成)
    interpretation: str | None = None


#WSAgentContext是一个Pydantic模型，定义了在整个图的执行过程中共享的上下文信息。
class WSAgentContext(BaseModel):    
    column_qdrant_repo: ColumnQdrantRepository
    metric_qdrant_repo: MetricQdrantRepository
    es_repo: ESRepository
    meta_db_repo: MetaDBRepository
    dw_db_repo: DWDBRepository
    #全是自定义类，所以需要在模型配置中设置arbitrary_types_allowed=True，
    #允许使用任意类型的字段。
    model_config = ConfigDict(arbitrary_types_allowed=True)


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