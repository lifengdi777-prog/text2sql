"""规则法表头检测(LLM 兜底)的单元测试。

运行：.venv/Scripts/python.exe test/test_header_heuristic.py
覆盖：标题+空行+单表头 / 标题+空行+两行合并表头 / 干净表 / 纯空表(判不准→None) /
      无表头纯数据 / 短行补齐。并端到端验证 dirty_headers.xlsx 真实读取无误读。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.header_detect import detect_header_heuristic, read_sheet_previews, detect_headers_heuristic  # noqa: E402
from services.excel_ingest import parse_workbook, _looks_like_misread_header  # noqa: E402

XLSX = Path(__file__).resolve().parent.parent / "test_data" / "dirty_headers.xlsx"


def G(*rows):
    """构造网格（每行一个 list[str]），并算 width。"""
    return list(rows), max(len(r) for r in rows)


# (说明, 网格, 期望 data_start_row 或 None, 期望 columns 或 None)
CASES = [
    ("标题+空行+单表头",
     G(["2024销售表", "", ""], ["", "", ""], ["地区", "产品", "金额"], ["华东", "手机", "120"]),
     3, ["地区", "产品", "金额"]),
    ("标题+空行+两行合并表头",
     G(["门店表", "", "", "", ""], ["", "", "", "", ""],
       ["门店", "销售", "", "客流", ""], ["", "金额", "笔数", "进店", "成交"],
       ["旗舰店", "320", "45", "1200", "300"]),
     4, ["门店", "销售金额", "销售笔数", "客流进店", "客流成交"]),
    ("干净表(表头在第0行)",
     G(["日期", "销量"], ["2025-01-01", "100"]),
     1, ["日期", "销量"]),
    ("无表头纯数据(首行即数据,退化为 header=第0行)",
     G(["华东", "120"], ["华北", "90"]),
     1, ["华东", "120"]),
    ("短行补齐(表头行比数据窄)",
     G(["标题", "", ""], ["地区", "金额"], ["华东", "1", "x"]),
     2, ["地区", "金额", "列3"]),
    ("全空网格→判不准",
     G(["", ""], ["", ""]),
     None, None),
]


def main() -> int:
    fails = 0
    for desc, (grid, width), want_start, want_cols in CASES:
        sh = detect_header_heuristic(grid, width)
        if want_start is None:
            ok = sh is None
        else:
            ok = sh is not None and sh.data_start_row == want_start and sh.columns == want_cols
        fails += not ok
        got = "None" if sh is None else f"start={sh.data_start_row} cols={sh.columns}"
        print(f"{'✅' if ok else '❌'} {desc}")
        if not ok:
            print(f"    期望 start={want_start} cols={want_cols} | 实际 {got}")

    # 端到端：真实脏表文件 → 规则法 → parse_workbook 应无误读
    print("\n--- 端到端 dirty_headers.xlsx ---")
    data = XLSX.read_bytes()
    specs = detect_headers_heuristic(read_sheet_previews(data))
    out, _ = parse_workbook(data, specs)
    for s, df in out.items():
        mis = _looks_like_misread_header(df)
        fails += mis
        print(f"{'✅' if not mis else '❌'} [{s}] 列={list(df.columns)} 误读={mis}")

    print(f"\n{'全部通过 🎉' if not fails else f'{fails} 个用例失败'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
