"""
Eval Report 生成器。

两种模式:

1) 单次报告:把一次 eval 结果渲染成 markdown 表格
    uv run python -m evals.report --result evals/baselines/20260522_120000_abc1234.json

2) 对比报告:跟历史基线 diff,标红回归项
    uv run python -m evals.report \
        --result evals/baselines/20260522_after.json \
        --baseline evals/baselines/20260522_before.json

报告默认打到 stdout,加 --out evals/report.md 可写文件。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_result(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _fmt_ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}ms"


def _arrow(curr: float | None, base: float | None, higher_is_better: bool = True) -> str:
    """返回 ↑/↓/→ 加变化幅度。"""
    if curr is None or base is None:
        return ""
    diff = curr - base
    if abs(diff) < 1e-6:
        return " (→0%)"
    if higher_is_better:
        arrow = "↑" if diff > 0 else "↓"
    else:
        arrow = "↓" if diff > 0 else "↑"
    return f" ({arrow}{abs(diff) * 100:.1f}pp)"


# ──────────────────────────────────────────────────────────────────────────
# 单次报告
# ──────────────────────────────────────────────────────────────────────────

def render_single_report(data: dict[str, Any]) -> str:
    summary = data["summary"]
    results = data["results"]
    git_sha = data.get("git_sha", "?")
    ts = data.get("timestamp", "?")

    lines: list[str] = []
    lines.append(f"# Eval Report — {ts} @ `{git_sha}`")
    lines.append("")

    # ── 总览
    overall = summary.get("overall", {})
    if overall:
        lines.append("## 总览(不含 safety)")
        lines.append("")
        lines.append(f"- 用例数: **{overall.get('count', 0)}**")
        lines.append(f"- Execution Accuracy: **{_fmt_pct(overall.get('execution_accuracy'))}**")
        lines.append(f"- Exact Match: **{_fmt_pct(overall.get('exact_match'))}**")
        lat = overall.get("latency", {})
        lines.append(f"- 延迟 p50/p95/avg: **{_fmt_ms(lat.get('p50'))} / "
                     f"{_fmt_ms(lat.get('p95'))} / {_fmt_ms(lat.get('avg'))}**")
        lines.append("")

    # ── 各难度档
    lines.append("## 各难度档")
    lines.append("")
    lines.append("| 难度 | 用例 | EX | EM | SL-Table | SL-Column | p50 | p95 |")
    lines.append("|------|-----:|-----:|-----:|---------:|----------:|----:|----:|")
    order = ["easy", "medium", "hard", "extra", "safety"]
    for diff in order:
        s = summary.get("by_difficulty", {}).get(diff)
        if not s:
            continue
        lat = s.get("latency", {})
        if diff == "safety":
            lines.append(
                f"| safety | {s['count']} | "
                f"pass {_fmt_pct(s.get('safety_pass_rate'))} | — | — | — | "
                f"{_fmt_ms(lat.get('p50'))} | {_fmt_ms(lat.get('p95'))} |"
            )
        else:
            lines.append(
                f"| {diff} | {s['count']} | "
                f"{_fmt_pct(s.get('execution_accuracy'))} | "
                f"{_fmt_pct(s.get('exact_match'))} | "
                f"{_fmt_pct(s.get('schema_linking_table_recall'))} | "
                f"{_fmt_pct(s.get('schema_linking_column_recall'))} | "
                f"{_fmt_ms(lat.get('p50'))} | {_fmt_ms(lat.get('p95'))} |"
            )
    lines.append("")

    # ── 类别切片(只看 EX)
    by_cat = _category_breakdown(results)
    if by_cat:
        lines.append("## 类别切片(Execution Accuracy)")
        lines.append("")
        lines.append("| 类别 | 用例 | EX |")
        lines.append("|------|-----:|-----:|")
        for cat, (total, hit) in sorted(by_cat.items(), key=lambda x: -x[1][0]):
            rate = hit / total if total else 0
            lines.append(f"| {cat} | {total} | {_fmt_pct(rate)} |")
        lines.append("")

    # ── 失败用例明细
    failed = [r for r in results if _is_failed(r)]
    if failed:
        lines.append(f"## ❌ 失败用例 ({len(failed)} 条)")
        lines.append("")
        for r in failed:
            lines.append(f"### [{r['id']}] {r['query']}")
            lines.append("")
            lines.append(f"- 难度/类别: `{r['difficulty']}` / `{r.get('category')}`")
            if r.get("error"):
                lines.append(f"- 错误: `{r['error']}`")
            if r["difficulty"] != "safety":
                ex = r.get("execution", {})
                em = r.get("exact_match", {})
                sl = r.get("schema_linking", {})
                lines.append(f"- EX: {ex.get('match')}, EM: {em.get('match')}, "
                             f"SL Table: {sl.get('table_recall')}, SL Column: {sl.get('column_recall')}")
                if sl.get("missing_tables"):
                    lines.append(f"- 缺失表: `{sl['missing_tables']}`")
                if sl.get("missing_columns"):
                    lines.append(f"- 缺失列: `{sl['missing_columns']}`")
                if ex.get("pred_error"):
                    lines.append(f"- Pred SQL 执行错误: `{ex['pred_error']}`")
            else:
                safety = r.get("safety", {})
                if safety.get("reasons"):
                    lines.append(f"- 原因: {safety['reasons']}")
            lines.append("")
            lines.append("**Gold SQL:**")
            lines.append("```sql")
            lines.append((r.get("gold_sql") or "").strip())
            lines.append("```")
            lines.append("")
            lines.append("**Pred SQL:**")
            lines.append("```sql")
            lines.append((r.get("pred_sql") or "(empty)").strip())
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def _is_failed(r: dict[str, Any]) -> bool:
    if r["difficulty"] == "safety":
        return r.get("safety", {}).get("pass") is False
    if r.get("error"):
        return True
    ex = r.get("execution", {}).get("match")
    return ex is False


def _category_breakdown(results: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """按 category 统计 (total, ex_hit)。仅算非 safety。"""
    out: dict[str, tuple[int, int]] = {}
    for r in results:
        if r["difficulty"] == "safety":
            continue
        cat = r.get("category") or "uncategorized"
        total, hit = out.get(cat, (0, 0))
        total += 1
        if r.get("execution", {}).get("match") is True:
            hit += 1
        out[cat] = (total, hit)
    return out


# ──────────────────────────────────────────────────────────────────────────
# 对比报告
# ──────────────────────────────────────────────────────────────────────────

def render_diff_report(curr: dict[str, Any], base: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Eval Diff Report")
    lines.append("")
    lines.append(f"- **Current**: {curr.get('timestamp')} @ `{curr.get('git_sha')}`")
    lines.append(f"- **Baseline**: {base.get('timestamp')} @ `{base.get('git_sha')}`")
    lines.append("")

    cs = curr["summary"]
    bs = base["summary"]

    # ── 总览对比
    co = cs.get("overall", {})
    bo = bs.get("overall", {})
    if co and bo:
        lines.append("## 总览对比")
        lines.append("")
        lines.append("| 指标 | Baseline | Current | Δ |")
        lines.append("|------|---------:|--------:|---:|")
        for name, key, hib in [
            ("Execution Accuracy", "execution_accuracy", True),
            ("Exact Match", "exact_match", True),
        ]:
            curr_v = co.get(key)
            base_v = bo.get(key)
            lines.append(
                f"| {name} | {_fmt_pct(base_v)} | {_fmt_pct(curr_v)} | "
                f"{_arrow(curr_v, base_v, higher_is_better=hib).strip() or '—'} |"
            )
        # 延迟越小越好
        cl = co.get("latency", {})
        bl = bo.get("latency", {})
        for name, key in [("p50 延迟", "p50"), ("p95 延迟", "p95")]:
            lines.append(
                f"| {name} | {_fmt_ms(bl.get(key))} | {_fmt_ms(cl.get(key))} | "
                f"{_latency_arrow(cl.get(key), bl.get(key))} |"
            )
        lines.append("")

    # ── 各难度档对比
    lines.append("## 各难度档对比")
    lines.append("")
    lines.append("| 难度 | EX (base→curr) | EM (base→curr) | SL-Table | SL-Column |")
    lines.append("|------|----------------|----------------|----------|-----------|")
    for diff in ["easy", "medium", "hard", "extra"]:
        c = cs.get("by_difficulty", {}).get(diff, {})
        b = bs.get("by_difficulty", {}).get(diff, {})
        if not c and not b:
            continue
        lines.append(
            f"| {diff} | "
            f"{_fmt_pct(b.get('execution_accuracy'))} → {_fmt_pct(c.get('execution_accuracy'))}"
            f"{_arrow(c.get('execution_accuracy'), b.get('execution_accuracy'))} | "
            f"{_fmt_pct(b.get('exact_match'))} → {_fmt_pct(c.get('exact_match'))}"
            f"{_arrow(c.get('exact_match'), b.get('exact_match'))} | "
            f"{_fmt_pct(b.get('schema_linking_table_recall'))} → "
            f"{_fmt_pct(c.get('schema_linking_table_recall'))}"
            f"{_arrow(c.get('schema_linking_table_recall'), b.get('schema_linking_table_recall'))} | "
            f"{_fmt_pct(b.get('schema_linking_column_recall'))} → "
            f"{_fmt_pct(c.get('schema_linking_column_recall'))}"
            f"{_arrow(c.get('schema_linking_column_recall'), b.get('schema_linking_column_recall'))} |"
        )
    lines.append("")

    # ── per-case 回归(在 baseline 通过 但 current 失败)
    base_index = {r["id"]: r for r in base["results"]}
    regressions: list[tuple[dict, dict]] = []
    improvements: list[tuple[dict, dict]] = []
    for r in curr["results"]:
        b = base_index.get(r["id"])
        if not b:
            continue
        if r["difficulty"] == "safety":
            curr_ok = r.get("safety", {}).get("pass") is True
            base_ok = b.get("safety", {}).get("pass") is True
        else:
            curr_ok = r.get("execution", {}).get("match") is True
            base_ok = b.get("execution", {}).get("match") is True
        if base_ok and not curr_ok:
            regressions.append((r, b))
        elif not base_ok and curr_ok:
            improvements.append((r, b))

    if regressions:
        lines.append(f"## 🚨 回归 ({len(regressions)} 条)")
        lines.append("")
        for r, _ in regressions:
            lines.append(f"- [{r['id']}] ({r['difficulty']}) {r['query']}")
        lines.append("")

    if improvements:
        lines.append(f"## ✅ 修复 ({len(improvements)} 条)")
        lines.append("")
        for r, _ in improvements:
            lines.append(f"- [{r['id']}] ({r['difficulty']}) {r['query']}")
        lines.append("")

    return "\n".join(lines)


def _latency_arrow(curr: float | None, base: float | None) -> str:
    if curr is None or base is None:
        return "—"
    diff = curr - base
    if abs(diff) < 1:
        return "→"
    return f"{'↑' if diff > 0 else '↓'}{abs(diff):.0f}ms"


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Render eval report")
    ap.add_argument("--result", type=str, required=True, help="path to eval result json")
    ap.add_argument("--baseline", type=str, default=None,
                    help="optional baseline result json to diff against")
    ap.add_argument("--out", type=str, default=None,
                    help="write markdown to this file (default: stdout)")
    args = ap.parse_args()

    curr = load_result(Path(args.result))
    if args.baseline:
        base = load_result(Path(args.baseline))
        md = render_diff_report(curr, base)
    else:
        md = render_single_report(curr)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"报告已写: {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
