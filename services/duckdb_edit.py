"""DuckDB 编辑引擎 —— 「智能助手」对 Excel 数据副本做增删改的底座。

与 [duckdb_exec.py](duckdb_exec.py) 互补:
  - duckdb_exec:只读、httpfs 直读 parquet 建**视图**(列裁剪/谓词下推,不落地)。
  - duckdb_edit:编辑要整表可变,故把 parquet **整份读进来物化成 DuckDB 表**,
    并附两列血缘 —— __row_id(行身份)/ __excel_row(原始 Excel 行号),供
    保样式回写([xlsx_export.py](xlsx_export.py))把变更定位到原件正确单元格。

状态模型(见 docs/dataset_edit_agent_design.md §2):无状态 + 操作日志重放。
每次用 EditWorkbook 物化一份内存副本,replay 已应用的 op,读完即 close。
原 parquet / 原件永不被写。
"""
from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd

from services import object_store
from services.duckdb_exec import _df_to_rows

# 血缘列:对 LLM 不可见(prompt/校验器约束它别碰),仅引擎与导出用。
ROW_ID = "__row_id"
EXCEL_ROW = "__excel_row"
META_COLS = (ROW_ID, EXCEL_ROW)


def _q(ident: str) -> str:
    """安全地把标识符包成 DuckDB 双引号形式。"""
    return '"' + str(ident).replace('"', '""') + '"'


def _data_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in META_COLS]


def _eq(a: Any, b: Any) -> bool:
    """单元格值相等判断(数值按 float 比,其余按字符串比,NaN==NaN)。"""
    a_na, b_na = pd.isna(a), pd.isna(b)
    if a_na or b_na:
        return a_na and b_na
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


class EditWorkbook:
    """一个数据集的可编辑内存副本:每个 sheet 物化成带血缘列的 DuckDB 表。"""

    def __init__(self, frames: dict[str, tuple[pd.DataFrame, dict | None]]):
        """frames[sheet] = (canonical_df(不含血缘列), lineage|None)。

        from_dataset() 会从对象存储读 parquet 拼出 frames;测试可直接传内存 df。
        """
        self.con = duckdb.connect(":memory:")
        self.lineage: dict[str, dict] = {}
        for name, (df, lin) in frames.items():
            self._materialize(name, df, lin)

    # ---- 构造 ----
    @classmethod
    def from_dataset(cls, info: dict) -> "EditWorkbook":
        """从 get_dataset_info() 的 info 物化:读各 sheet parquet + 取 schema 里的 lineage。"""
        folder = info["folder_path"]
        sheets = (info.get("schema") or {}).get("sheets") or {}
        frames: dict[str, tuple[pd.DataFrame, dict | None]] = {}
        for name, sinfo in sheets.items():
            pq = sinfo.get("parquet_file")
            if not pq:
                continue
            df = object_store.read_df_parquet(f"{folder}/{pq}")
            frames[name] = (df, sinfo.get("lineage"))
        return cls(frames)

    def _materialize(self, name: str, df: pd.DataFrame, lin: dict | None) -> None:
        df = df.copy()
        n = len(df)
        lin = lin or {}
        # row_origin:原始 Excel 行号;缺失/长度不符 → 退化为顺序行号(2 起,header 占 1 行)
        row_origin = lin.get("row_origin")
        if not row_origin or len(row_origin) != n:
            row_origin = list(range(2, 2 + n))
        # __row_id 用 'x'+excel_row(原始行稳定可复现);__excel_row 供回写定位
        df.insert(0, EXCEL_ROW, [int(r) for r in row_origin])
        df.insert(0, ROW_ID, [f"x{int(r)}" for r in row_origin])
        # 加回被剔除的合计/汇总行(仅编辑可见;问数 parquet 不含它们 → 问数零影响)。
        # 它们带原始 excel_row,故是"原件已存在的行",编辑后导出走改值/删行(写回原位),不是新增行。
        extra = lin.get("extra_rows") or []
        if extra:
            data_cols = [c for c in df.columns if c not in META_COLS]
            rows = []
            for e in extra:
                vals = e.get("values") or {}
                row = {c: vals.get(c) for c in data_cols}
                row[EXCEL_ROW] = int(e["excel_row"])
                row[ROW_ID] = f"x{int(e['excel_row'])}"
                rows.append(row)
            extra_df = pd.DataFrame(rows, columns=df.columns)
            df = pd.concat([df, extra_df], ignore_index=True)
        self.con.register("_src", df)
        self.con.execute(f"CREATE TABLE {_q(name)} AS SELECT * FROM _src")
        self.con.unregister("_src")
        self.lineage[name] = lin

    # ---- 编辑 ----
    def replay(self, ops: list[str]) -> None:
        """按序执行 op SQL(DML/DDL),执行完给新插入行补 __row_id。"""
        for sql in ops:
            self.con.execute(sql)
        self._assign_new_row_ids()

    def _assign_new_row_ids(self) -> None:
        # INSERT 进来的新行 __row_id 为 NULL(LLM 只写数据列)→ 补 uuid(本次物化内稳定)。
        # __excel_row 留 NULL → 导出时识别为"原件没有的新行",走 insert_rows。
        for name in self.sheets():
            try:
                self.con.execute(
                    f"UPDATE {_q(name)} SET {ROW_ID} = uuid()::VARCHAR "
                    f"WHERE {ROW_ID} IS NULL"
                )
            except duckdb.Error:
                pass  # 该表可能被 DDL 改过结构,补 id 失败不致命

    # ---- 读 ----
    def sheets(self) -> list[str]:
        return list(self.lineage.keys())

    def current(self, sheet: str) -> pd.DataFrame:
        """当前态全表(含血缘列),供 diff 比对。"""
        return self.con.execute(f"SELECT * FROM {_q(sheet)}").fetch_df()

    def preview(self, sheet: str, limit: int = 100) -> dict:
        """对外预览:隐藏血缘列,返回 {columns, rows, total}。"""
        df = self.current(sheet)
        cols = _data_cols(df)
        view = df[cols].head(limit)
        return {"columns": cols, "rows": _df_to_rows(view), "total": int(len(df))}

    def columns_now(self, sheet: str) -> list[str]:
        """当前数据列(DDL 改过列后,喂给 LLM 的实时结构用,不含血缘列)。"""
        return _data_cols(self.current(sheet))

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "EditWorkbook":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def diff_sheet(before: pd.DataFrame, after: pd.DataFrame, lineage: dict | None) -> dict:
    """对比同一 sheet 的前后态(按 __row_id),产出 cell-level diff。

    返回:
      cell_changes: [{row_id, excel_row, col, old, new}]   —— 改值
      deleted:      [{row_id, excel_row}]                   —— 删行(有原始 Excel 行)
      new_rows:     [{row_id, values:{列:值}}]              —— 新增行(无原始 Excel 行)
      added_cols / dropped_cols / renames                   —— 列结构变化
    rename 检测:dropped 列 D 与 added 列 N 若逐行值全相等 → 视为重命名(避免删列丢数据)。
    """
    cols0, cols1 = _data_cols(before), _data_cols(after)
    raw_added = [c for c in cols1 if c not in cols0]
    raw_dropped = [c for c in cols0 if c not in cols1]

    b0 = {r[ROW_ID]: r for _, r in before.iterrows()}
    b1 = {r[ROW_ID]: r for _, r in after.iterrows()}
    common_ids = [rid for rid in b1 if rid in b0]

    # rename 检测:在 (dropped × added) 里找逐行值全等的配对
    renames: list[dict] = []
    used_added: set[str] = set()
    col_excel = (lineage or {}).get("col_excel") or {}
    for d in list(raw_dropped):
        for a in raw_added:
            if a in used_added:
                continue
            if all(_eq(b0[r][d], b1[r][a]) for r in common_ids):
                renames.append({"old": d, "new": a, "excel_col": col_excel.get(d)})
                used_added.add(a)
                raw_dropped.remove(d)
                break
    added_cols = [c for c in raw_added if c not in used_added]
    dropped_cols = raw_dropped

    deleted = [{"row_id": rid, "excel_row": _to_int(b0[rid][EXCEL_ROW])}
               for rid in b0 if rid not in b1]

    new_rows = [{"row_id": rid,
                 "values": {c: _jsonable(b1[rid][c]) for c in cols1}}
                for rid in b1 if rid not in b0]

    cell_changes = []
    for rid in common_ids:
        r0, r1 = b0[rid], b1[rid]
        for c in cols0:
            if c in cols1 and not _eq(r0[c], r1[c]):
                cell_changes.append({
                    "row_id": rid,
                    "excel_row": _to_int(r0[EXCEL_ROW]),
                    "col": c,
                    "old": _jsonable(r0[c]),
                    "new": _jsonable(r1[c]),
                })

    return {
        "cell_changes": cell_changes,
        "deleted": deleted,
        "new_rows": new_rows,
        "added_cols": added_cols,
        "dropped_cols": dropped_cols,
        "renames": renames,
    }


def _to_int(v: Any) -> int | None:
    return None if pd.isna(v) else int(v)


def _jsonable(v: Any) -> Any:
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            return str(v)
    return v
