"""跨表补全端到端验证：用真实 Excel 两个 sheet 建 EditWorkbook，跑"补客户城市/会员等级到订单明细"。

复刻 generate_sql→validate_sql→apply 的执行路径（不依赖 DB/对象存储/LLM）：
  1) 读 xlsx 两 sheet → frames → EditWorkbook（验证多 sheet 同库）
  2) 候选 SQL 过 validate_edit_sql（验证放行 + target_sheet）
  3) before/after 双副本 replay + diff_sheet（复刻 apply_and_diff）
  4) 断言：城市/会员等级被正确按 客户ID JOIN 填入

运行：.venv/Scripts/python.exe test/test_crosssheet_enrich_e2e.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from services.duckdb_edit import EditWorkbook, diff_sheet  # noqa: E402
from services.edit_sql_guard import validate_edit_sql  # noqa: E402

XLSX = Path(__file__).resolve().parent.parent / "test_data" / "ecommerce_orders.xlsx"

ENRICH_SQL = (
    'ALTER TABLE "订单明细" ADD COLUMN "城市" VARCHAR;'
    'ALTER TABLE "订单明细" ADD COLUMN "会员等级" VARCHAR;'
    'UPDATE "订单明细" SET "城市"=c."城市", "会员等级"=c."会员等级" '
    'FROM "客户信息" c WHERE "订单明细"."客户ID"=c."客户ID"'
)


def load_frames() -> dict:
    xl = pd.ExcelFile(XLSX)
    return {name: (xl.parse(name), None) for name in xl.sheet_names}


def main() -> int:
    frames = load_frames()
    print("sheets:", list(frames))
    known = set(frames)

    # 1) 校验放行
    chk = validate_edit_sql(ENRICH_SQL, known, protected_sheets=known)
    assert chk.ok, f"校验未通过: {chk.issues}"
    assert chk.target_sheet == "订单明细", f"target 错: {chk.target_sheet}"
    print(f"✅ 校验放行，写入目标 = {chk.target_sheet}, op_type={chk.op_type}")

    # 2) 复刻 apply_and_diff：before/after 双副本
    before = EditWorkbook(load_frames())
    after = EditWorkbook(load_frames())
    try:
        after.replay([chk.normalized_sql])
        diff = diff_sheet(before.current("订单明细"), after.current("订单明细"),
                          after.lineage.get("订单明细"))
        print(f"✅ 执行成功，新增列 = {diff['added_cols']}，改值单元格 = {len(diff['cell_changes'])}")

        # 3) 校验正确性：抽样比对 JOIN 结果
        cust = frames["客户信息"][0].set_index("客户ID")
        after_df = after.current("订单明细")
        checked = 0
        for _, row in after_df.head(20).iterrows():
            cid = row["客户ID"]
            if cid in cust.index:
                assert row["城市"] == cust.loc[cid, "城市"], f"{cid} 城市不符"
                assert row["会员等级"] == cust.loc[cid, "会员等级"], f"{cid} 会员等级不符"
                checked += 1
        assert checked > 0, "没抽到可比对的行"
        filled = after_df["城市"].notna().sum()
        print(f"✅ JOIN 正确：抽样 {checked} 行城市/会员等级全对；共 {filled}/{len(after_df)} 行已填充")
    finally:
        before.close()
        after.close()

    print("\n端到端通过 🎉 —— 跨表补全在真实数据上跑通")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
