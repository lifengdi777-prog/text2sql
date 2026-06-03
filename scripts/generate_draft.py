"""草稿生成——对**任意数据源**自动产出一份 meta_config 草稿。

定位:这是 init_data.py 的"反面"。init_data 是【读 json → 灌库】,
本模块是【读库 → 生成 json 草稿】,且**不写任何库、不碰 Qdrant/ES**。

- `generate_draft(datasource_id)`:可复用函数,返回草稿 dict(供 API/materialize 调用)。
- 直接跑脚本:对指定数据源跑一遍并打印报告;ds_default 还会跟手写版对比。

跑法:  uv run python -m scripts.generate_draft [datasource_id]   # 不传则 ds_default
产出:  conf/meta_config.draft[.<datasource_id>].json + 终端对比报告
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from agent.llm import llm, fast_llm
from agent.prompts import load_prompt
from clients.mysql import dw_mysql_client, client_registry
from conf.app_config import DEFAULT_DATASOURCE_ID
from conf.meta_config import MetaConfig
from repositories.mysql import DWDBRepository
from langchain_core.messages import SystemMessage, HumanMessage

# 数值类型 → 默认猜成 measure(度量);其余非主外键列默认猜 dimension(维度)。
NUMERIC_TYPES = {
    "int", "tinyint", "smallint", "mediumint", "bigint",
    "decimal", "numeric", "float", "double", "real", "bit",
}
# 自由文本/二进制类型:值不是可枚举的命名实体(备注、长描述、邮箱等),
# 索引进 ES 又大又没用,默认不 sync。普通 varchar/char 维度则默认 sync。
FREE_TEXT_TYPES = {"text", "tinytext", "mediumtext", "longtext", "blob",
                   "tinyblob", "mediumblob", "longblob", "json"}


def _draft_path(datasource_id: str) -> Path:
    # ds_default 沿用老文件名;其它源带上 id 区分。
    if datasource_id == DEFAULT_DATASOURCE_ID:
        return Path("conf/meta_config.draft.json")
    return Path(f"conf/meta_config.draft.{datasource_id}.json")


# ───────────────────────── ① 代码确定性探测 + 启发式 ─────────────────────────
async def introspect(session, tables: list[str] | None = None) -> dict[str, Any]:
    """只读 information_schema + 采样,产出每张表每列的物理事实 + 启发式猜测。

    tables 给定时只探测这些表(用户在向导里勾选的子集);为 None 则探测全库。
    """
    repo = DWDBRepository(session)

    # 1. 表清单(含库里原有表注释)
    table_rows = (await session.execute(text(
        "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
        "ORDER BY TABLE_NAME"
    ))).fetchall()
    table_comments = {r[0]: (r[1] or "") for r in table_rows}
    table_names = [r[0] for r in table_rows]
    if tables is not None:
        allow = set(tables)
        table_names = [t for t in table_names if t in allow]

    # 2. 列清单(类型 / 主键标记 / 库里原有列注释),按定义顺序
    col_rows = (await session.execute(text(
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY, COLUMN_COMMENT "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    ))).fetchall()

    # 3. 外键边:① DB 声明的(information_schema) ② 命名约定推断
    #    很多库(尤其本项目的星型库)不在 DB 里建 FK 约束,关系全靠"外键列名==被引用表主键名"
    #    的命名约定(如 table_order.customer_id ↔ table_customer.customer_id)。
    #    两者合并:声明的优先,声明没覆盖到的按命名补。推断出的边同时用于判 foreign_key 角色。
    declared = await repo.get_foreign_keys()
    declared_cols = {(e["from_table"], e["from_column"]) for e in declared}

    # 每张表自己的主键列(避免把本表主键误判成外键) + "主键列名→表"(只留全局唯一的名,防歧义)
    pk_of_table: dict[str, set[str]] = {}
    pk_name_tables: dict[str, set[str]] = {}
    for tname, cname, _dtype, ckey, _cc in col_rows:
        if ckey == "PRI":
            pk_of_table.setdefault(tname, set()).add(cname)
            pk_name_tables.setdefault(cname, set()).add(tname)
    pk_name_to_table = {name: next(iter(ts)) for name, ts in pk_name_tables.items() if len(ts) == 1}

    fks = [dict(e, source="declared") for e in declared]
    for tname, cname, _dtype, _ckey, _cc in col_rows:
        if cname in pk_of_table.get(tname, set()):
            continue  # 本表主键,不是外键
        ref = pk_name_to_table.get(cname)
        if ref and ref != tname and (tname, cname) not in declared_cols:
            fks.append({"from_table": tname, "from_column": cname,
                        "to_table": ref, "to_column": cname, "source": "inferred"})

    fk_cols = {(e["from_table"], e["from_column"]) for e in fks}
    n_inf = sum(1 for e in fks if e.get("source") == "inferred")
    print(f"   外键边: 声明 {len(declared)} 条 + 命名推断 {n_inf} 条 = {len(fks)} 条")

    tables: list[dict[str, Any]] = []
    for tname in table_names:
        my_cols = [r for r in col_rows if r[0] == tname]
        col_dicts: list[dict[str, Any]] = []
        measure_count = 0
        for _, cname, dtype, ckey, ccomment in my_cols:
            is_numeric = dtype.lower() in NUMERIC_TYPES
            # 列角色启发式:主键/外键确定;数值非键→measure;其余→dimension
            if ckey == "PRI":
                role = "primary_key"
            elif (tname, cname) in fk_cols:
                role = "foreign_key"
            elif is_numeric:
                role = "measure"
                measure_count += 1
            else:
                role = "dimension"

            # 采样值(去重,最多 20 个)
            examples = (await repo.get_column_values(tname, [cname], limit=20))[cname]
            examples = list(dict.fromkeys(examples))  # 保序去重

            # sync:该列的"值"要不要灌进 ES 做召回。
            # 判定轴不是"值多不多",而是"用户会不会在问题里点名这个值"——
            # 命名实体维度(product_name/customer_name…)正是高基数却最该进 ES。
            # 代码默认:普通字符串维度 → true;自由文本类型 → false(备注/长描述等);
            # 主外键、度量天然不进。真正的边界情况留给人审最终定夺。
            sync = role == "dimension" and not is_numeric and dtype.lower() not in FREE_TEXT_TYPES

            col_dicts.append({
                "name": cname,
                "type": dtype,
                "role_guess": role,
                "comment": ccomment or "",
                "examples": examples,
                "sync": sync,
            })

        # 表角色启发式:有度量列→fact;否则 dim(bridge 较难自动判,交给 LLM 修正)
        fk_in_table = sum(1 for c in col_dicts if c["role_guess"] == "foreign_key")
        non_key = sum(1 for c in col_dicts if c["role_guess"] in ("dimension", "measure"))
        if measure_count > 0:
            role_guess = "fact"
        elif fk_in_table >= 2 and non_key <= 1:
            role_guess = "bridge"
        else:
            role_guess = "dim"

        tables.append({
            "name": tname,
            "role_guess": role_guess,
            "comment": table_comments.get(tname, ""),
            "columns": col_dicts,
        })

    # 外键边也带上,供 LLM 起草单表时拼"邻居关联"上下文
    return {"tables": tables, "foreign_keys": fks}


# ───────────────────────── ② LLM 起草业务语义 ─────────────────────────
def _parse_json(content: str) -> dict:
    content = content.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", content, re.S)
    if m:
        content = m.group(1).strip()
    return json.loads(content)


# 起草用快模型:description/alias 这类简单结构化任务不需要推理模型。
# 推理模型(llm)每次调用有很高的固定延迟,并发也救不回来;快模型(fast_llm)延迟低很多。
DRAFT_LLM = fast_llm
# 并发上限:别一次把几十个请求全砸给 LLM 服务(限流/超时)。
_LLM_SEMAPHORE = asyncio.Semaphore(12)


async def _llm_json(system: str, user: str, label: str) -> dict:
    """调一次 LLM 拿 JSON,并打印该请求自身耗时(用于看到底哪个请求慢)。"""
    t = time.perf_counter()
    async with _LLM_SEMAPHORE:
        resp = await DRAFT_LLM.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    out = _parse_json(resp.content)  # type: ignore
    print(f"   · {label:<26} {time.perf_counter() - t:5.1f}s")
    return out


def _relations_for(tname: str, fks: list[dict[str, str]]) -> list[str]:
    """这张表涉及的外键边,拼成人话给 LLM 当关联上下文。"""
    rels = []
    for fk in fks:
        if fk["from_table"] == tname:
            rels.append(f"本表.{fk['from_column']} → {fk['to_table']}.{fk['to_column']}(本表引用它)")
        elif fk["to_table"] == tname:
            rels.append(f"{fk['from_table']}.{fk['from_column']} → 本表.{fk['to_column']}(它引用本表)")
    return rels


async def _draft_one_table(table: dict[str, Any], fks: list[dict[str, str]], system: str) -> tuple[str, dict]:
    """起草单张表的语义。失败不致命:返回空 dict,merge 时回退到库内注释。"""
    tname = table["name"]
    payload = {
        "table": tname,
        "role_guess": table["role_guess"],
        "comment": table["comment"],
        "columns": [
            {"name": c["name"], "type": c["type"], "role_guess": c["role_guess"],
             "comment": c["comment"], "examples": c["examples"]}
            for c in table["columns"]
        ],
        "relations": _relations_for(tname, fks),
    }
    try:
        out = await _llm_json(system, json.dumps(payload, ensure_ascii=False), f"表 {tname}")
        return tname, out
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠ 表 {tname} 起草失败,已跳过:{e}")
        return tname, {}


async def _draft_metrics(schema: dict[str, Any], system: str) -> list[dict]:
    """基于所有度量列提炼候选指标(单次调用,与单表起草并发)。"""
    measures = [
        {"id": f"{t['name']}.{c['name']}", "type": c["type"],
         "comment": c["comment"], "examples": c["examples"]}
        for t in schema["tables"] for c in t["columns"] if c["role_guess"] == "measure"
    ]
    if not measures:
        return []
    payload = {
        "tables": [{"name": t["name"], "role_guess": t["role_guess"], "comment": t["comment"]}
                   for t in schema["tables"]],
        "measures": measures,
    }
    try:
        out = await _llm_json(system, json.dumps(payload, ensure_ascii=False), "指标")
        return out.get("metrics", [])
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠ 指标起草失败,已跳过:{e}")
        return []


async def draft_semantics(schema: dict[str, Any]) -> dict[str, Any]:
    """按表并发起草 + 指标单独一趟,全部并发跑。

    相比"整库塞一个请求",这里每个请求只吐一张表的量,且 asyncio.gather 并发,
    总耗时≈最慢的那一张,表越多优势越大。
    """
    table_sys = await load_prompt("draft_table")
    metric_sys = await load_prompt("draft_metrics")
    fks = schema.get("foreign_keys", [])

    # 单表任务 + 指标任务,一起并发
    table_tasks = [_draft_one_table(t, fks, table_sys) for t in schema["tables"]]
    results = await asyncio.gather(*table_tasks, _draft_metrics(schema, metric_sys))

    table_results: list[tuple[str, dict]] = results[:-1]  # type: ignore
    metrics: list[dict] = results[-1]  # type: ignore
    return {
        "tables": {tname: out for tname, out in table_results},
        "metrics": metrics,
    }


# ───────────────────────── 合并成 meta_config 形状 ─────────────────────────
def merge_to_meta_config(schema: dict[str, Any], llm_out: dict[str, Any]) -> dict[str, Any]:
    """代码事实(role/sync) + LLM 语义(description/alias/metric)合并,校验列引用。"""
    valid_roles = {"dim", "fact", "bridge"}
    llm_tables = llm_out.get("tables", {})
    real_column_ids: set[str] = set()

    out_tables = []
    for t in schema["tables"]:
        tname = t["name"]
        lt = llm_tables.get(tname, {})
        lt_cols = lt.get("columns", {})
        llm_role = lt.get("role")
        role = llm_role if llm_role in valid_roles else t["role_guess"]

        out_cols = []
        for c in t["columns"]:
            cid = f"{tname}.{c['name']}"
            real_column_ids.add(cid)
            lc = lt_cols.get(c["name"], {})
            out_cols.append({
                "name": c["name"],
                "role": c["role_guess"],            # 角色以代码为准(主外键确定,维度/度量启发式)
                "description": lc.get("description", c["comment"] or ""),
                "alias": lc.get("alias", []) if c["role_guess"] not in ("primary_key", "foreign_key") else [],
                "sync": c["sync"],
            })
        out_tables.append({
            "name": tname,
            "role": role,
            "description": lt.get("description", t["comment"] or ""),
            "columns": out_cols,
        })

    # 校验 metric.relevant_columns:对不上真实列的整条丢弃,并报告
    out_metrics, dropped = [], []
    for m in llm_out.get("metrics", []):
        cols = m.get("relevant_columns", [])
        bad = [c for c in cols if c not in real_column_ids]
        if bad:
            dropped.append((m.get("name", "?"), bad))
            continue
        out_metrics.append({
            "name": m.get("name", ""),
            "description": m.get("description", ""),
            "relevant_columns": cols,
            "alias": m.get("alias", []),
        })

    return {"tables": out_tables, "metrics": out_metrics, "_dropped_metrics": dropped}


# ───────────────────────── 可复用入口:返回草稿 dict ─────────────────────────
async def generate_draft(datasource_id: str = DEFAULT_DATASOURCE_ID,
                         tables: list[str] | None = None) -> dict[str, Any]:
    """对指定数据源产出 meta_config 草稿(introspect + LLM 起草 + 合并校验)。

    tables 给定时只起草这些表(向导里勾选的子集);None 则全库。
    返回 {tables, metrics, _dropped_metrics}:tables/metrics 已可直接给前端审核;
    _dropped_metrics 是被丢弃的幻觉指标(供报告)。**不写任何库、不碰 Qdrant/ES、不关连接。**
    连接经 client_registry 按 datasource_id 解析,任意已注册数据源都能用。
    """
    client = await client_registry.get_client(datasource_id)
    async with client.session() as session:
        schema = await introspect(session, tables)
    llm_out = await draft_semantics(schema)
    return merge_to_meta_config(schema, llm_out)


# ───────────────────────── 跟手写版对比报告 ─────────────────────────
def compare_report(draft: dict[str, Any], datasource_id: str = DEFAULT_DATASOURCE_ID) -> None:
    print("\n" + "=" * 70)
    print("草稿 vs 手写 meta_config.json 对比")
    print("=" * 70)

    gold = None
    gold_tables: dict[str, Any] = {}
    gold_path = Path("conf/meta_config.json")
    # 只有 ds_default 才有手工金标准可比;其它数据源没有 gold,只展示草稿。
    if datasource_id == DEFAULT_DATASOURCE_ID and gold_path.exists():
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        gold_tables = {t["name"]: t for t in gold["tables"]}
    else:
        print(f"(数据源 {datasource_id} 无手工 meta_config.json 可比,仅展示草稿)")

    for t in draft["tables"]:
        gt = gold_tables.get(t["name"])
        print(f"\n■ 表 {t['name']}  role: 草稿={t['role']}" +
              (f" / 手写={gt['role']}" if gt else ""))
        print(f"  描述(草稿): {t['description']}")
        if gt:
            print(f"  描述(手写): {gt['description']}")
        gcols = {c["name"]: c for c in gt["columns"]} if gt else {}
        for c in t["columns"]:
            gc = gcols.get(c["name"])
            print(f"   · {c['name']} [{c['role']}] sync={c['sync']}")
            print(f"       desc(草稿): {c['description']}")
            if gc:
                print(f"       desc(手写): {gc['description']}")
                missing = [a for a in gc.get("alias", []) if a not in c["alias"]]
                if missing:
                    print(f"       ⚠ 手写有、草稿漏的别名: {missing}")

    print(f"\n■ LLM 提出的候选指标({len(draft['metrics'])} 个):")
    for m in draft["metrics"]:
        print(f"   · {m['name']}  ← {m['relevant_columns']}")
        print(f"       {m['description']}")
        if m["alias"]:
            print(f"       别名: {m['alias']}")
    if draft.get("_dropped_metrics"):
        print("\n   ⚠ 因引用了不存在的列被丢弃的指标:")
        for name, bad in draft["_dropped_metrics"]:
            print(f"      - {name}: {bad}")
    if gold:
        print(f"\n   (手写版有 {len(gold.get('metrics', []))} 个指标作参考)")


async def main():
    # 命令行第一个参数 = datasource_id(不传则 ds_default)
    datasource_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATASOURCE_ID
    print(f"数据源: {datasource_id}")
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    try:
        client = await client_registry.get_client(datasource_id)
        async with client.session() as session:
            print("① 探测物理结构 + 启发式...")
            t = time.perf_counter()
            schema = await introspect(session)
            timings["① introspect(读库+采样+启发式)"] = time.perf_counter() - t
            print(f"   探测到 {len(schema['tables'])} 张表  (耗时 {timings['① introspect(读库+采样+启发式)']:.1f}s)")

        n_tables = len(schema["tables"])
        print(f"② 调 LLM 起草业务语义(按表并发 {n_tables} 个 + 指标 1 个,共 {n_tables + 1} 个请求)...")
        t = time.perf_counter()
        llm_out = await draft_semantics(schema)
        timings["② LLM 起草(并发)"] = time.perf_counter() - t
        print(f"   (耗时 {timings['② LLM 起草(并发)']:.1f}s)")

        print("③ 合并 + 校验列引用...")
        t = time.perf_counter()
        draft = merge_to_meta_config(schema, llm_out)

        # 校验草稿是否符合 MetaConfig 形状(剔除内部字段后)
        clean = {"tables": draft["tables"], "metrics": draft["metrics"]}
        MetaConfig.model_validate(clean)
        draft_path = _draft_path(datasource_id)
        draft_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        timings["③ 合并+校验+落盘"] = time.perf_counter() - t
        print(f"   ✓ 草稿已写入 {draft_path}  (耗时 {timings['③ 合并+校验+落盘']:.1f}s)")

        compare_report(draft, datasource_id)

        # 耗时汇总:看哪一步最慢、整体能不能接受
        total = time.perf_counter() - t0
        print("\n" + "=" * 70)
        print("耗时汇总")
        print("=" * 70)
        for name, sec in timings.items():
            print(f"  {name:<28} {sec:6.1f}s  ({sec / total * 100:4.1f}%)")
        print(f"  {'合计':<28} {total:6.1f}s")
    finally:
        # 关掉按需建出的连接池 + 默认 DW 池(脚本退出前清理)
        await client_registry.close_all()
        await dw_mysql_client.close()


if __name__ == "__main__":
    asyncio.run(main())
