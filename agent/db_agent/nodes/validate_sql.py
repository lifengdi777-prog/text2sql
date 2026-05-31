from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from core.log import logger
import sqlglot
from sqlglot import exp


class SQLValidationError(ValueError):
    pass


# 结果行数上限:防 LLM 写出无 LIMIT 的全表查询把整张表拉进内存/推给前端。
MAX_RESULT_ROWS = 1000


def cap_limit(sql: str, cap: int = MAX_RESULT_ROWS, dialect: str = "mysql") -> str:
    """给 SQL 注入/收紧 LIMIT 到 cap+1(用 +1 让执行环节能判断"是否还有更多 → 截断")。

    · 无 LIMIT 或 LIMIT 比上限大 → 设成 cap+1;
    · 用户已写了更小的 LIMIT(如 top 5)→ 保留,不动。
    用 sqlglot 在顶层语句上设 LIMIT(保留内层 ORDER BY,不像"包子查询"那样在 MySQL 丢排序)。
    """
    try:
        stmt = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return sql
    if not isinstance(stmt, (exp.Select, exp.Union)):
        return sql
    target = cap + 1
    lim = stmt.args.get("limit")
    cur: int | None = None
    if lim is not None and lim.expression is not None:
        try:
            cur = int(lim.expression.this)
        except (TypeError, ValueError):
            cur = None
    if cur is None or cur > target:
        stmt = stmt.limit(target)
    return stmt.sql(dialect=dialect)


def validate_readonly_sql(sql: str, dialect: str = "mysql") -> None:
    #首先检查SQL字符串是否为空或仅包含空白字符
    if not sql or not sql.strip():
        raise SQLValidationError("SQL安全校验失败：SQL不能为空。")

    try:
        expressions = sqlglot.parse(sql, read=dialect)
    except Exception as exc:
        raise SQLValidationError(f"SQL安全校验失败：SQL语法解析失败，原因：{exc}") from exc
    #检查解析结果是否只是一条sql语句
    if len(expressions) != 1:
        raise SQLValidationError("SQL安全校验失败：一次只允许执行一条SQL。")

    #取出唯一一条sql语句先判断是否为空
    expression = expressions[0]
    if expression is None:
        raise SQLValidationError("SQL安全校验失败：SQL解析结果为空。")
    #再判断语句类型是否为只读查询
    if not isinstance(expression, (exp.Select, exp.Union)):
        statement_type = expression.__class__.__name__
        raise SQLValidationError(
            f"SQL安全校验失败：检测到非查询语句 {statement_type}。系统仅允许单条只读SELECT查询。"
        )

#判断SQL语句是否有语法错误，是否符合规范。
async def validate_sql(state: WSAgentState, runtime: Runtime[WSAgentContext]):
    writer = runtime.stream_writer
    writer(WSStepInfo(step="校验SQL语句", status="running"))

    sql = state.sql or ""

    try:
        #第一步先做安全校验
        validate_readonly_sql(sql, dialect="mysql")
        #第二步再做语法校验(短会话:只在 EXPLAIN 绑定校验时占用连接)
        async with runtime.context.dw_repo() as dw_db_repo:
            await dw_db_repo.validate_sql(sql)
        #第三步:注入/收紧 LIMIT,防无界全表查询(写回 state.sql,执行用规范化后的)
        capped = cap_limit(sql)
        if capped != sql:
            logger.info(f"SQL 注入行数上限:{capped}")
        writer(WSStepInfo(step="校验SQL语句", status="success"))
        logger.info("sql校验成功！")
        #没有错误就清空error字段,带着规范化后的 SQL 继续执行;
        return {"error": None, "sql": capped}
    except Exception as e:
        logger.info(f"sql校验失败！错误信息：{e}")
        writer(WSStepInfo(step="校验SQL语句", status="error"))
        #如果有错误，就把错误信息放到error字段里，进入校正流程。
        return {"error": str(e)}