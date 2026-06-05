"""「智能助手」编辑 SQL 的静态安全校验器(sqlglot,零 DB)。

与只读问数的 [validate_sql](../agent/dataset_agent/nodes/validate_sql.py) 正好相反:
那边只放行 SELECT、禁一切;这边**放行 DML + 受限 DDL**,但守住安全边界。

放行:
  - SELECT / UNION(对当前编辑态提问)
  - INSERT / UPDATE / DELETE(增删改;INSERT 必须显式列出列名)
  - ALTER TABLE … ADD COLUMN / DROP COLUMN / RENAME COLUMN(决策 2:加/删/改列)

拦截:
  - DROP TABLE / CREATE / ATTACH / COPY / PRAGMA / SET / 其它命令类语句
  - 读写文件函数(read_parquet / read_csv / glob …,防越权读写文件/对象存储)
  - 引用引擎内部血缘列(__row_id / __excel_row)
  - 跨 sheet:整条指令涉及的表必须 ≤ 1 个、且都是已知 sheet(决策 1)

标记(不拦,交上层处理):
  - UPDATE / DELETE 不带 WHERE → needs_confirm(防"误删/误改全表",前端二次确认)

绑定校验(表/列/语法是否真的成立)不在这里做:编辑副本是一次性可丢的内存表,
apply 阶段直接执行并 catch 异常即是绑定校验,无需额外 EXPLAIN。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from services.duckdb_edit import META_COLS

# 读写文件类函数:LLM 只能操作我们建好的表,不能读写任意文件/对象存储路径。
_BLOCKED_FUNCS = {
    "read_parquet", "parquet_scan", "read_csv", "read_csv_auto", "csv_scan",
    "read_json", "read_json_auto", "read_json_objects", "read_ndjson",
    "read_text", "read_blob", "glob", "parquet_metadata", "parquet_schema",
}

# ALTER 只放行这三类 action(对应加列 / 删列 / 改列名)
_ALLOWED_ALTER_ACTIONS = {"ColumnDef", "Drop", "RenameColumn"}

# 顶层语句白名单(其余一律拦)
_DML_TYPES = (exp.Insert, exp.Update, exp.Delete)
_READ_TYPES = (exp.Select, exp.Union)


@dataclass
class EditSQLCheck:
    ok: bool
    issues: list[str] = field(default_factory=list)
    op_type: str = "unknown"          # select/insert/update/delete/alter/mixed
    target_sheet: str | None = None
    needs_confirm: bool = False        # 无 WHERE 的 UPDATE/DELETE
    normalized_sql: str = ""           # sqlglot 重新序列化(多语句用 ; 连接)


def validate_edit_sql(sql: str, known_sheets: set[str],
                      protected_sheets: set[str] | None = None) -> EditSQLCheck:
    """校验一条(或多条)编辑 SQL。

    known_sheets:**可引用**的 sheet(原始数据 sheet + 会话内已建的汇总表)——能读/能改。
    protected_sheets:**不可被 CREATE 覆盖**的 sheet(仅原始数据 sheet)。
      不传 → 默认等于 known_sheets(严格:任何已知表都不让 CREATE 覆盖)。
    """
    issues: list[str] = []
    protected = protected_sheets if protected_sheets is not None else known_sheets
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception as exc:
        return EditSQLCheck(ok=False, issues=[f"SQL 解析失败:{exc}"])
    if not statements:
        return EditSQLCheck(ok=False, issues=["未检测到任何语句"])

    op_kinds: set[str] = set()
    tables_all: set[str] = set()
    create_targets: set[str] = set()
    needs_confirm = False

    for st in statements:
        kind = _classify(st, issues)
        if kind:
            op_kinds.add(kind)

        # CREATE 的目标表是「新建的汇总 sheet」,允许是新名字,不参与"必须已知"校验;
        # 但不能用 CREATE 覆盖已有数据 sheet。
        create_target = None
        if isinstance(st, exp.Create) and st.this is not None:
            create_target = st.this.name
            create_targets.add(create_target)
            if create_target in protected:
                issues.append(f"不能用 CREATE 覆盖原始数据表「{create_target}」(请换个汇总表名)")

        # 其余引用的表:必须都是已知 sheet(CREATE 目标除外)
        st_tables = {t.name for t in st.find_all(exp.Table)}
        for t in st_tables:
            if t == create_target:
                continue
            if t not in known_sheets:
                issues.append(f"引用了未知的表「{t}」(只能操作本数据集的 sheet)")
        tables_all |= st_tables - ({create_target} if create_target else set())

        # 读写文件函数
        for fn in st.find_all(exp.Func):
            name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
            if name in _BLOCKED_FUNCS:
                issues.append(f"不允许调用读写文件函数:{name}")

        # 引擎内部血缘列不可碰
        for ident in st.find_all(exp.Identifier):
            if ident.name in META_COLS:
                issues.append(f"不允许引用内部列:{ident.name}")

        # INSERT 必须显式列出列名(否则会错位写到血缘列)
        if isinstance(st, exp.Insert) and not _insert_has_columns(st):
            issues.append("INSERT 必须显式列出要写入的列名,如 INSERT INTO t (a,b) VALUES …")

        # 无 WHERE 的 UPDATE/DELETE → 标记待确认(不拦)
        if isinstance(st, (exp.Update, exp.Delete)) and st.args.get("where") is None:
            needs_confirm = True

    # 跨 sheet:整条指令涉及的(已知)源表 > 1 → 拦(CREATE 目标不算源)
    known_touched = tables_all & known_sheets
    if len(known_touched) > 1:
        issues.append(f"暂不支持跨 sheet 操作(本次涉及:{', '.join(sorted(known_touched))})")

    # target_sheet:建汇总表 → 取新建的汇总表名;否则取唯一的已知源表
    if create_targets:
        target_sheet = next(iter(create_targets))
    elif len(known_touched) == 1:
        target_sheet = next(iter(known_touched))
    else:
        target_sheet = None

    mutating = op_kinds & {"insert", "update", "delete", "alter", "summary"}
    if not mutating:
        op_type = "select" if op_kinds else "unknown"
    elif len(mutating) == 1:
        op_type = next(iter(mutating))
    else:
        op_type = "mixed"

    normalized = "; ".join(st.sql(dialect="duckdb") for st in statements)
    return EditSQLCheck(
        ok=not issues,
        issues=issues,
        op_type=op_type,
        target_sheet=target_sheet,
        needs_confirm=needs_confirm,
        normalized_sql=normalized,
    )


def _classify(st: exp.Expression, issues: list[str]) -> str | None:
    """判断单条语句类型;非白名单 → 记 issue 并返回 None。"""
    if isinstance(st, _READ_TYPES):
        return "select"
    if isinstance(st, exp.Insert):
        return "insert"
    if isinstance(st, exp.Update):
        return "update"
    if isinstance(st, exp.Delete):
        return "delete"
    if isinstance(st, exp.Alter):
        actions = st.args.get("actions") or []
        bad = [type(a).__name__ for a in actions if type(a).__name__ not in _ALLOWED_ALTER_ACTIONS]
        if bad:
            issues.append(f"ALTER 只支持加列/删列/改列名,检测到不支持的操作:{bad}")
            return None
        return "alter"
    # 受控建汇总表:只放行 CREATE [OR REPLACE] TABLE … AS SELECT(决策 9),其它 CREATE 一律拦
    if isinstance(st, exp.Create):
        if st.args.get("kind") == "TABLE" and isinstance(st.expression, exp.Select):
            return "summary"
        issues.append('只支持 CREATE TABLE "汇总" AS SELECT …(建汇总表),不支持其它 CREATE')
        return None
    issues.append(f"不允许的语句类型:{type(st).__name__}(只能增删改 / 加删改列 / 建汇总表 / 查询)")
    return None


def _insert_has_columns(st: exp.Insert) -> bool:
    """INSERT 是否带显式列名(this 为 Schema 即带列名;为 Table 则没有)。"""
    this = st.this
    return isinstance(this, exp.Schema) and bool(this.expressions)
