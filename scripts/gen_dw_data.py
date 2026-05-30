"""
DW 业务库测试数据生成脚本
===========================

为 Text2SQL 系统生成一份内容更丰富的 `dw.sql`，相比手写版本补齐了：

1. 时间跨度  : 默认覆盖 2025、2026 两整年（按周采样），支持同比(YoY)/环比(MoM/QoQ)/趋势查询。
2. 记录密度  : 每个生产日期多条记录，TOP N、分组聚合更有区分度。
3. 业务真实性: 同比增长 + 月度季节性 + 合格量=实际产量-不良数量 + 设备状态影响停机/不良率。
4. 异常注入  : 可控比例的离群记录（高不良、长停机、生产事故），用于演示"数据解读发现异常"。
5. 空值注入  : 可控比例的 NULL（仅 downtime_minutes / production_hours），用于演示 NULL 处理。
6. 外键一致  : 事实表的所有维度 ID 一定落在维表内；可选给 DDL 加真实 FOREIGN KEY 约束。

维表（产品/车间产线/设备/工序）保持与原 dw.sql 完全一致，因为它们的取值已和
conf/meta_config.json、Elasticsearch 字段值、字段别名绑定。要扩维表请同时同步元数据并重跑 init_data。

用法:
    python scripts/gen_dw_data.py
输出:
    docker/mysql/dw.sql  (默认覆盖；建议生成后用 git diff 检查)
"""

import random
from datetime import date, timedelta
from pathlib import Path

# ============================ 配置区（按需调整） ============================

SEED = 42                                   # 随机种子，固定后每次生成结果一致，便于复现/对比
YEARS = [2025, 2026]                        # 要生成的年份；保留两年才能做同比
WEEKLY_START = date(2025, 1, 6)             # 起始日期（建议取周一）
WEEKLY_END = date(2026, 12, 28)            # 结束日期
STEP_DAYS = 7                               # 采样步长：7=每周一条日期；改 1 则每天
RECORDS_PER_DATE = (6, 12)                  # 每个生产日期生成的记录数范围（含两端）

# 同比增长系数：让 2026 整体高于 2025，YoY 查询能看到增长趋势
YEAR_GROWTH = {2025: 1.00, 2026: 1.15}
# 月度季节性系数：模拟淡旺季（2 月春节最低，Q4 旺季最高）
MONTH_SEASONALITY = {
    1: 0.95, 2: 0.80, 3: 1.05, 4: 1.10, 5: 1.08, 6: 1.12,
    7: 1.00, 8: 0.98, 9: 1.10, 10: 1.15, 11: 1.18, 12: 1.05,
}

OUTLIER_RATE = 0.03                         # 异常记录比例（高不良/长停机/生产事故）
NULL_RATE = 0.02                            # 空值比例（仅注入到 downtime_minutes / production_hours）

ADD_FOREIGN_KEYS = True                     # 是否给事实表 DDL 加真实外键约束（数据已保证一致，安全）
INSERT_BATCH = 200                          # 每条 INSERT 语句的 VALUES 行数

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docker" / "mysql" / "dw.sql"

# ============================ 维表数据（与原 dw.sql 一致） ============================

# 产品维表：(id, 名称, 类别, 型号, 基准日产量, 基准不良率)
# 基准日产量/不良率仅用于生成事实表，不写入维表，决定该产品的量级与质量水平，让排名稳定可解释。
PRODUCTS = [
    ("P001", "工业机器人控制器", "自动化设备", "IRC-200", 560, 0.028),
    ("P002", "动力电池模组", "新能源部件", "BAT-48V", 450, 0.040),
    ("P003", "伺服电机", "驱动部件", "SVM-750", 720, 0.022),
    ("P004", "精密齿轮", "机械零件", "GR-35", 800, 0.025),
    ("P005", "逆变器模块", "电力电子", "INV-10K", 390, 0.048),
    ("P006", "液压泵组件", "液压系统", "HYP-120", 450, 0.045),
    ("P007", "传感器模块", "智能传感", "SNS-8", 950, 0.020),
    ("P008", "铝合金支架", "结构件", "BRK-AL", 880, 0.018),
]

# 车间产线维表：(id, 工厂, 车间, 产线)
WORKSHOPS = [
    ("W001", "华东工厂", "一车间", "A线"),
    ("W002", "华东工厂", "一车间", "B线"),
    ("W003", "华东工厂", "二车间", "C线"),
    ("W004", "华南工厂", "装配车间", "D线"),
    ("W005", "华北工厂", "机加车间", "E线"),
    ("W006", "西南工厂", "总装车间", "F线"),
]

# 设备维表：(id, 名称, 类型, 状态)
# 状态影响生成：运行=正常，维护=停机偏高，停机=停机最高且不良率上浮。
EQUIPMENTS = [
    ("E001", "数控车床-01", "数控机床", "运行"),
    ("E002", "激光切割机-02", "激光设备", "运行"),
    ("E003", "自动装配线-03", "装配设备", "运行"),
    ("E004", "焊接机器人-04", "焊接设备", "维护"),
    ("E005", "老化测试台-05", "测试设备", "运行"),
    ("E006", "包装机-06", "包装设备", "运行"),
    ("E007", "注塑机-07", "成型设备", "停机"),
    ("E008", "AOI检测机-08", "检测设备", "运行"),
]

# 工序维表：(id, 名称, 类型)
PROCESSES = [
    ("PR001", "下料", "准备工序"),
    ("PR002", "机加工", "加工工序"),
    ("PR003", "焊接", "连接工序"),
    ("PR004", "装配", "装配工序"),
    ("PR005", "测试", "检验工序"),
    ("PR006", "包装", "入库工序"),
]

# 设备状态 → (停机基准分钟, 不良率上浮倍数)
STATUS_EFFECT = {
    "运行": (8, 1.0),
    "维护": (35, 1.3),
    "停机": (60, 1.6),
}

# ============================ 工具函数 ============================


def quarter_of(month: int) -> str:
    return f"Q{(month - 1) // 3 + 1}"


def gen_dates():
    """按 STEP_DAYS 步长生成日期维度，只保留 YEARS 内的日期。"""
    rows = []
    d = WEEKLY_START
    while d <= WEEKLY_END:
        if d.year in YEARS:
            date_id = d.year * 10000 + d.month * 100 + d.day
            rows.append((date_id, d.year, quarter_of(d.month), d.month, d.day))
        d += timedelta(days=STEP_DAYS)
    return rows


def gen_facts(date_rows, rng: random.Random):
    """生成生产记录事实表。返回 list[tuple]。"""
    facts = []
    for date_id, year, _quarter, month, _day in date_rows:
        growth = YEAR_GROWTH.get(year, 1.0)
        season = MONTH_SEASONALITY.get(month, 1.0)
        n = rng.randint(*RECORDS_PER_DATE)
        for seq in range(1, n + 1):
            product = rng.choice(PRODUCTS)
            workshop = rng.choice(WORKSHOPS)
            equipment = rng.choice(EQUIPMENTS)
            process = rng.choice(PROCESSES)

            _pid, _pname, _cat, _model, base_vol, base_defect = product
            status = equipment[3]
            down_base, defect_mult = STATUS_EFFECT[status]

            # 计划产量：基准量 × 同比 × 季节性 × 噪声
            planned = base_vol * growth * season * rng.uniform(0.90, 1.10)
            planned = max(50, int(round(planned)))

            # 实际产量：达成率约 0.99 ± 0.04，偶尔超额
            achievement = rng.gauss(0.99, 0.04)
            actual = max(1, int(round(planned * achievement)))

            # 不良率：产品基准 × 设备状态上浮 × 噪声
            defect_rate = base_defect * defect_mult * rng.uniform(0.7, 1.3)
            # 停机：状态基准 + 噪声
            downtime = max(0, int(round(down_base + rng.gauss(0, down_base * 0.4))))
            # 生产工时
            prod_hours = round(rng.uniform(6.5, 8.5), 1)

            # —— 异常注入：随机挑一种异常形态 ——
            if rng.random() < OUTLIER_RATE:
                kind = rng.choice(["high_defect", "long_downtime", "incident"])
                if kind == "high_defect":
                    defect_rate *= rng.uniform(3.0, 5.0)          # 质量事故：不良率飙升
                elif kind == "long_downtime":
                    downtime = int(downtime + rng.uniform(180, 400))  # 长时间停机
                else:  # incident
                    actual = max(1, int(actual * rng.uniform(0.4, 0.6)))  # 生产事故：实际远低于计划

            defect = min(actual, max(0, int(round(actual * defect_rate))))
            qualified = actual - defect

            # —— 空值注入：仅作用于软指标，绝不动主键/外键/核心产量 ——
            downtime_val = None if rng.random() < NULL_RATE else downtime
            hours_val = None if rng.random() < NULL_RATE else prod_hours

            record_id = f"PRD{date_id}{seq:03d}"
            facts.append((
                record_id, product[0], workshop[0], equipment[0], process[0], date_id,
                planned, actual, qualified, defect, downtime_val, hours_val,
            ))
    return facts


def sql_val(v) -> str:
    """把 Python 值渲染成 SQL 字面量。"""
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def emit_insert(table: str, columns, rows) -> str:
    """生成分批的 INSERT 语句。"""
    if not rows:
        return ""
    col_str = ", ".join(columns)
    parts = []
    for i in range(0, len(rows), INSERT_BATCH):
        chunk = rows[i:i + INSERT_BATCH]
        values = ",\n".join(
            "       (" + ", ".join(sql_val(c) for c in row) + ")" for row in chunk
        )
        parts.append(f"INSERT INTO {table} ({col_str})\nVALUES\n{values};")
    return "\n\n".join(parts)


# ============================ 主流程 ============================


def build_sql() -> str:
    rng = random.Random(SEED)
    date_rows = gen_dates()
    fact_rows = gen_facts(date_rows, rng)

    fk_clause = ""
    if ADD_FOREIGN_KEYS:
        fk_clause = """,
    CONSTRAINT fk_pr_product   FOREIGN KEY (product_id)   REFERENCES table_product (product_id),
    CONSTRAINT fk_pr_workshop  FOREIGN KEY (workshop_id)  REFERENCES table_workshop (workshop_id),
    CONSTRAINT fk_pr_equipment FOREIGN KEY (equipment_id) REFERENCES table_equipment (equipment_id),
    CONSTRAINT fk_pr_process   FOREIGN KEY (process_id)   REFERENCES table_process (process_id),
    CONSTRAINT fk_pr_date      FOREIGN KEY (date_id)      REFERENCES table_date (date_id)"""

    blocks = []
    blocks.append("SET NAMES utf8mb4;\n")
    blocks.append("DROP DATABASE IF EXISTS dw;")
    blocks.append("CREATE DATABASE dw DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;")
    blocks.append("USE dw;\n")

    blocks.append("DROP TABLE IF EXISTS table_production_record;")
    blocks.append("DROP TABLE IF EXISTS table_product;")
    blocks.append("DROP TABLE IF EXISTS table_workshop;")
    blocks.append("DROP TABLE IF EXISTS table_equipment;")
    blocks.append("DROP TABLE IF EXISTS table_process;")
    blocks.append("DROP TABLE IF EXISTS table_date;\n")

    # 产品维表
    blocks.append("""CREATE TABLE table_product
(
    product_id       VARCHAR(20) PRIMARY KEY,
    product_name     VARCHAR(100),
    product_category VARCHAR(50),
    product_model    VARCHAR(50)
);""")
    blocks.append(emit_insert(
        "table_product",
        ["product_id", "product_name", "product_category", "product_model"],
        [(p[0], p[1], p[2], p[3]) for p in PRODUCTS],
    ))

    # 车间产线维表
    blocks.append("""CREATE TABLE table_workshop
(
    workshop_id   VARCHAR(20) PRIMARY KEY,
    factory_name  VARCHAR(50),
    workshop_name VARCHAR(50),
    line_name     VARCHAR(50)
);""")
    blocks.append(emit_insert(
        "table_workshop",
        ["workshop_id", "factory_name", "workshop_name", "line_name"],
        WORKSHOPS,
    ))

    # 设备维表
    blocks.append("""CREATE TABLE table_equipment
(
    equipment_id     VARCHAR(20) PRIMARY KEY,
    equipment_name   VARCHAR(100),
    equipment_type   VARCHAR(50),
    equipment_status VARCHAR(20)
);""")
    blocks.append(emit_insert(
        "table_equipment",
        ["equipment_id", "equipment_name", "equipment_type", "equipment_status"],
        EQUIPMENTS,
    ))

    # 工序维表
    blocks.append("""CREATE TABLE table_process
(
    process_id   VARCHAR(20) PRIMARY KEY,
    process_name VARCHAR(50),
    process_type VARCHAR(50)
);""")
    blocks.append(emit_insert(
        "table_process",
        ["process_id", "process_name", "process_type"],
        PROCESSES,
    ))

    # 日期维表
    blocks.append("""CREATE TABLE table_date
(
    date_id INT PRIMARY KEY,
    year    INT,
    quarter VARCHAR(2),
    month   INT,
    day     INT
);""")
    blocks.append(emit_insert(
        "table_date",
        ["date_id", "year", "quarter", "month", "day"],
        date_rows,
    ))

    # 生产记录事实表
    blocks.append(f"""CREATE TABLE table_production_record
(
    record_id          VARCHAR(30) PRIMARY KEY,
    product_id         VARCHAR(20),
    workshop_id        VARCHAR(20),
    equipment_id       VARCHAR(20),
    process_id         VARCHAR(20),
    date_id            INT,
    planned_quantity   INT,
    actual_quantity    INT,
    qualified_quantity INT,
    defect_quantity    INT,
    downtime_minutes   INT,
    production_hours   FLOAT{fk_clause}
);""")
    blocks.append(emit_insert(
        "table_production_record",
        ["record_id", "product_id", "workshop_id", "equipment_id", "process_id", "date_id",
         "planned_quantity", "actual_quantity", "qualified_quantity", "defect_quantity",
         "downtime_minutes", "production_hours"],
        fact_rows,
    ))

    sql = "\n\n".join(b for b in blocks if b) + "\n"
    return sql, date_rows, fact_rows


def main():
    sql, date_rows, fact_rows = build_sql()
    OUTPUT_PATH.write_text(sql, encoding="utf-8")
    print(f"已生成: {OUTPUT_PATH}")
    print(f"  日期维度行数 : {len(date_rows)}  (年份: {YEARS}, 步长: {STEP_DAYS} 天)")
    print(f"  生产记录行数 : {len(fact_rows)}")
    print(f"  外键约束     : {'已添加' if ADD_FOREIGN_KEYS else '未添加'}")
    print(f"  异常注入比例 : {OUTLIER_RATE:.0%}  空值注入比例: {NULL_RATE:.0%}")


if __name__ == "__main__":
    main()
