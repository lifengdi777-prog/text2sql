"""跨表补全（一写多读）的校验器边界测试。

直接运行：.venv/Scripts/python.exe test/test_edit_sql_guard_crosssheet.py
放行 = 读任意张 sheet 补全，但一次只写一张；拦截 = 写入多张 / 引用未知表 / 越权。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.edit_sql_guard import validate_edit_sql  # noqa: E402

KNOWN = {"订单明细", "客户信息"}        # 可读写的 sheet
PROTECTED = {"订单明细", "客户信息"}    # 原始数据 sheet（不可被 CREATE 覆盖）

# (说明, SQL, 期望ok, 期望target_sheet 或 None=不校验)
CASES = [
    # ── 应放行：一写多读 ──
    ("VLOOKUP 补列（ALTER+UPDATE…FROM）",
     'ALTER TABLE "订单明细" ADD COLUMN "城市" VARCHAR;'
     'UPDATE "订单明细" SET "城市"=c."城市" '
     'FROM "客户信息" c WHERE "订单明细"."客户ID"=c."客户ID"',
     True, "订单明细"),
    ("建合并宽表（CREATE AS SELECT … LEFT JOIN）",
     'CREATE OR REPLACE TABLE "订单宽表" AS '
     'SELECT o.*, c."城市", c."会员等级" FROM "订单明细" o '
     'LEFT JOIN "客户信息" c ON o."客户ID"=c."客户ID"',
     True, "订单宽表"),
    ("INSERT … SELECT FROM 另一张表",
     'INSERT INTO "订单明细" ("订单号") SELECT "客户ID" FROM "客户信息" WHERE "年龄">60',
     True, "订单明细"),
    ("单表改值（回归：原能力不受影响）",
     'UPDATE "订单明细" SET "订单状态"=\'待付款\' WHERE "订单状态"=\'待支付\'',
     True, "订单明细"),

    # ── 应拦截 ──
    ("写入两张表（两条 UPDATE 改不同表）",
     'UPDATE "订单明细" SET "城市"=\'x\';UPDATE "客户信息" SET "城市"=\'y\'',
     False, None),
    ("UPDATE 写到了非目标（写客户信息、读订单明细）后又改订单明细",
     'UPDATE "客户信息" SET "城市"=o."地区" FROM "订单明细" o WHERE "客户信息"."客户ID"=o."客户ID";'
     'UPDATE "订单明细" SET "地区"=\'z\'',
     False, None),
    ("引用未知表",
     'UPDATE "订单明细" SET "城市"=x."城市" FROM "未知表" x WHERE "订单明细"."客户ID"=x."客户ID"',
     False, None),
    ("CREATE 覆盖原始数据 sheet",
     'CREATE OR REPLACE TABLE "订单明细" AS SELECT * FROM "客户信息"',
     False, None),
    ("越权读文件函数",
     'CREATE OR REPLACE TABLE "x" AS SELECT * FROM read_csv(\'/etc/passwd\')',
     False, None),
]


def main() -> int:
    fails = 0
    for desc, sql, want_ok, want_target in CASES:
        chk = validate_edit_sql(sql, KNOWN, protected_sheets=PROTECTED)
        ok_pass = chk.ok == want_ok
        tgt_pass = want_target is None or chk.target_sheet == want_target
        passed = ok_pass and tgt_pass
        fails += not passed
        mark = "✅" if passed else "❌"
        print(f"{mark} {desc}")
        if not passed:
            print(f"    期望 ok={want_ok} target={want_target} | "
                  f"实际 ok={chk.ok} target={chk.target_sheet} issues={chk.issues}")
    print(f"\n{'全部通过 🎉' if not fails else f'{fails} 个用例失败'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
