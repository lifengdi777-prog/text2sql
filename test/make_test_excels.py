"""生成两个测试 Excel 用于验证上传/清洗/入库链路。

运行:uv run python -m test.make_test_excels
输出:
  test_data/normal_production.xlsx   正常表(表头第 0 行,2 个 sheet,含千分位/百分号/合计行)
  test_data/sales_with_dates.xlsx    带日期 + 高基数列(测时间维度 + top_K)
"""
from pathlib import Path

from openpyxl import Workbook

OUT_DIR = Path("test_data")


def make_normal_production() -> Path:
    """正常生产数据:2 个 sheet,含常见脏数据特征。"""
    wb = Workbook()

    # Sheet 1:生产明细 —— 千分位/百分号/合计行
    ws1 = wb.active
    ws1.title = "生产明细"
    ws1.append(["工厂", "产品", "产量", "合格率", "不良率"])
    rows1 = [
        ("华东工厂", "A 型", "13,478", "98.5%", "1.5%"),
        ("华东工厂", "B 型", "9,061",  "97.2%", "2.8%"),
        ("华北工厂", "A 型", "7,452",  "96.8%", "3.2%"),
        ("华北工厂", "B 型", "5,820",  "95.4%", "4.6%"),
        ("华南工厂", "A 型", "4,231",  "98.1%", "1.9%"),
        ("华南工厂", "C 型", "3,560",  "94.5%", "5.5%"),
        ("西南工厂", "A 型", "2,588",  "97.8%", "2.2%"),
        ("西南工厂", "C 型", "1,890",  "93.7%", "6.3%"),
        ("合计",    "",     "48,080", "",      ""),     # ← 应被去掉
    ]
    for r in rows1:
        ws1.append(r)

    # Sheet 2:设备台账 —— 字符串/日期混合
    ws2 = wb.create_sheet("设备台账")
    ws2.append(["设备编号", "设备类型", "状态", "启用日期"])
    rows2 = [
        ("E001", "注塑机",   "运行", "2024-01-15"),
        ("E002", "注塑机",   "维护", "2024-03-20"),
        ("E003", "数控机床", "运行", "2025-06-01"),
        ("E004", "检测设备", "停机", "2023-11-10"),
        ("E005", "数控机床", "运行", "2025-09-12"),
    ]
    for r in rows2:
        ws2.append(r)

    path = OUT_DIR / "normal_production.xlsx"
    wb.save(path)
    return path


def make_sales_with_dates() -> Path:
    """销售流水:测时间列(temporal) + 高基数产品列(top_K)+ 数值范围。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "销售明细"
    ws.append(["日期", "工厂", "产品名称", "销量", "单价"])

    import random
    random.seed(42)
    factories = ["华东工厂", "华北工厂", "华南工厂", "西南工厂"]
    products = [f"Product-{i:03d}" for i in range(1, 81)]   # 80 个产品(>50,触发 top_K)
    days = ["2025-01-15", "2025-02-20", "2025-03-10", "2025-04-05", "2025-05-22"]

    for _ in range(120):
        ws.append((
            random.choice(days),
            random.choice(factories),
            random.choice(products),
            random.randint(10, 500),
            round(random.uniform(99, 9999), 2),
        ))

    path = OUT_DIR / "sales_with_dates.xlsx"
    wb.save(path)
    return path


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    p1 = make_normal_production()
    p2 = make_sales_with_dates()
    print(f"[OK] {p1}")
    print(f"     2 sheet(生产明细 / 设备台账),含千分位/百分号/合计行")
    print(f"[OK] {p2}")
    print(f"     1 sheet(销售明细 120 行),含 80 个产品(触发 top_K)+ 日期列(temporal)")


if __name__ == "__main__":
    main()
