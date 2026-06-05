"""数据清洗：校验边界 + 真实数据端到端。

运行：.venv/Scripts/python.exe test/test_data_cleaning.py
覆盖：值标准化 / 去空格 / 缺失值(固定·整列均值·同组均值) / 去重 / 异常值 / 类型转换。
并断言 ALTER COLUMN TYPE 放行、而 SET DEFAULT / SET NOT NULL 仍被拦。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from services.duckdb_edit import EditWorkbook  # noqa: E402
from services.edit_sql_guard import validate_edit_sql  # noqa: E402

XLSX = Path(__file__).resolve().parent.parent / "test_data" / "ecommerce_orders.xlsx"


def _frames():
    xl = pd.ExcelFile(XLSX)
    return {n: (xl.parse(n), None) for n in xl.sheet_names}


# 应放行且能执行的清洗
RUNNABLE = [
    ("值标准化", 'UPDATE "订单明细" SET "地区"=\'华南\' WHERE "地区" IN (\'华南区\',\'华南地区\')'),
    ("去空格", 'UPDATE "订单明细" SET "商品名称"=TRIM("商品名称")'),
    ("缺失值·固定", 'UPDATE "订单明细" SET "金额"=0 WHERE "金额" IS NULL'),
    ("缺失值·整列均值",
     'UPDATE "订单明细" SET "单价"=(SELECT ROUND(AVG("单价"),2) FROM "订单明细" WHERE "单价" IS NOT NULL) WHERE "单价" IS NULL'),
    ("缺失值·同组均值",
     'UPDATE "订单明细" SET "单价"=(SELECT ROUND(AVG("单价"),2) FROM "订单明细" x '
     'WHERE x."品类"="订单明细"."品类" AND x."单价" IS NOT NULL) WHERE "单价" IS NULL'),
    ("去重·原地",
     'DELETE FROM "订单明细" WHERE rowid NOT IN (SELECT min(rowid) FROM "订单明细" GROUP BY "订单号")'),
    ("去重·建新表",
     'CREATE OR REPLACE TABLE "订单去重" AS SELECT * FROM "订单明细" '
     'QUALIFY row_number() OVER (PARTITION BY "订单号" ORDER BY "下单日期")=1'),
    ("异常值删除", 'DELETE FROM "订单明细" WHERE "金额"<0 OR "金额">100000'),
    ("类型转换", 'ALTER TABLE "订单明细" ALTER COLUMN "数量" TYPE INTEGER'),
]

# 应被拦：改类型以外的 ALTER COLUMN 不放行
BLOCKED = [
    ("ALTER COLUMN SET DEFAULT", 'ALTER TABLE "订单明细" ALTER COLUMN "数量" SET DEFAULT 0'),
    ("ALTER COLUMN SET NOT NULL", 'ALTER TABLE "订单明细" ALTER COLUMN "数量" SET NOT NULL'),
]


def main() -> int:
    frames = _frames()
    K = set(frames)
    fails = 0

    for desc, sql in RUNNABLE:
        chk = validate_edit_sql(sql, K, protected_sheets=K)
        ok = chk.ok
        run = ""
        if ok and chk.op_type != "select":
            wb = EditWorkbook({n: (df.copy(), l) for n, (df, l) in frames.items()})
            try:
                wb.replay([chk.normalized_sql])
            except Exception as e:
                ok, run = False, f" 执行失败:{str(e)[:60]}"
            finally:
                wb.close()
        fails += not ok
        print(f"{'✅' if ok else '❌'} 放行+执行 [{desc}] op={chk.op_type}"
              f"{'' if ok else ' ' + (str(chk.issues) if chk.issues else run)}")

    for desc, sql in BLOCKED:
        chk = validate_edit_sql(sql, K, protected_sheets=K)
        blocked = not chk.ok
        fails += not blocked
        print(f"{'✅' if blocked else '❌'} 拦截 [{desc}] {chk.issues if blocked else '竟被放行!'}")

    print(f"\n{'全部通过 🎉' if not fails else f'{fails} 个用例失败'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
