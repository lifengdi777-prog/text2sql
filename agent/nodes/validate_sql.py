from langgraph.runtime import Runtime
from agent.schemas import WSAgentState, WSAgentContext, WSStepInfo
from core.log import logger
import sqlglot
from sqlglot import exp


class SQLValidationError(ValueError):
    pass


def validate_readonly_sql(sql: str, dialect: str = "mysql") -> None:
    if not sql or not sql.strip():
        raise SQLValidationError("SQL安全校验失败：SQL不能为空。")

    try:
        expressions = sqlglot.parse(sql, read=dialect)
    except Exception as exc:
        raise SQLValidationError(f"SQL安全校验失败：SQL语法解析失败，原因：{exc}") from exc

    if len(expressions) != 1:
        raise SQLValidationError("SQL安全校验失败：一次只允许执行一条SQL。")

    expression = expressions[0]
    if expression is None:
        raise SQLValidationError("SQL安全校验失败：SQL解析结果为空。")

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

    dw_db_repo = runtime.context.dw_db_repo

    try:
        validate_readonly_sql(sql, dialect="mysql")
        await dw_db_repo.validate_sql(sql)
        writer(WSStepInfo(step="校验SQL语句", status="success"))
        logger.info("sql校验成功！")
        #没有错误就清空error字段，继续执行SQL；
        return {"error": None}
    except Exception as e:
        logger.info(f"sql校验失败！错误信息：{e}")
        writer(WSStepInfo(step="校验SQL语句", status="error"))
        #如果有错误，就把错误信息放到error字段里，进入校正流程。
        return {"error": str(e)}