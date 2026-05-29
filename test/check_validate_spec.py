"""离线自检:验证 ComputeSpec 的 validate_spec ⇄ correct_spec 校验/纠正逻辑。

运行:uv run python -m test.check_validate_spec

不依赖 LLM / MySQL / ES:
  - mock 掉 validate_spec 里打 MySQL 的 get_dataset_info(喂一份假 schema)
  - stream_writer 用空实现
专测「规则裁判 + difflib 自动纠正 + 路由分支」这套核心逻辑。
全部通过 → 退出码 0;任一失败 → 退出码 1(可放进 CI / pre-push)。
"""
from __future__ import annotations

import sys

# Windows 控制台默认 cp950,打印中文 / loguru 日志会崩 ——
# 必须在 import 项目模块(会配置 loguru sink)之前就把流切成 utf-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

import asyncio

import agent.dataset_agent.nodes.validate_spec as vmod
from agent.dataset_agent.graph import _route_after_validate
from agent.dataset_agent.nodes.correct_spec import MAX_RETRY
from agent.dataset_agent.schemas import DatasetAgentState


# ── 假数据集 schema:真实列 = 工厂 / 产量 / 日期 ──
_SCHEMA = {"sheets": {"生产明细": {"columns": [
    {"name": "工厂"}, {"name": "产量"}, {"name": "日期"},
]}}}


async def _fake_get_dataset_info(dataset_id: int):
    return {"schema": _SCHEMA}


class _FakeRuntime:
    """validate_spec 只用到 runtime.stream_writer(...),给个空实现即可。"""
    def stream_writer(self, info):
        pass


async def _validate(spec, retry: int = 0) -> dict:
    state = DatasetAgentState(messages=[], dataset_id=1, compute_spec=spec, spec_retry_count=retry)
    return await vmod.validate_spec(state, _FakeRuntime())


# ── 断言收集 ──
_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(label)


async def _run_validate_cases() -> None:
    print("validate_spec(规则裁判 + difflib 自动纠正):")

    # 1) 列名拼错 → difflib 自动纠正,不报 issue、不调 LLM
    r = await _validate({"sheet": "生产明细", "groupby": ["工厂"],
                         "aggregations": [{"col": "产量合计", "func": "sum"}]})
    _check("列名拼错自动纠正(产量合计→产量)",
           r["spec_issues"] == [] and r["compute_spec"]["aggregations"][0]["col"] == "产量",
           f"col={r['compute_spec']['aggregations'][0]['col']}")

    # 2) sheet 拼错 → 自动纠正
    r = await _validate({"sheet": "生产明细表",
                         "aggregations": [{"col": "产量", "func": "sum"}]})
    _check("sheet 拼错自动纠正(生产明细表→生产明细)",
           r["spec_issues"] == [] and r["compute_spec"]["sheet"] == "生产明细",
           f"sheet={r['compute_spec']['sheet']}")

    # 3) groupby 拼错 → 自动纠正
    r = await _validate({"sheet": "生产明细", "groupby": ["工"],
                         "aggregations": [{"col": "产量", "func": "sum"}]})
    _check("groupby 拼错自动纠正(工→工厂)",
           r["spec_issues"] == [] and r["compute_spec"]["groupby"] == ["工厂"],
           f"groupby={r['compute_spec']['groupby']}")

    # 4) order_by 引用聚合 alias、拼错 → 针对「结果列」纠正
    r = await _validate({"sheet": "生产明细", "groupby": ["工厂"],
                         "aggregations": [{"col": "产量", "func": "sum", "alias": "总产量"}],
                         "order_by": [{"col": "总产", "dir": "desc"}]})
    _check("order_by 按聚合结果列纠正(总产→总产量)",
           r["spec_issues"] == [] and r["compute_spec"]["order_by"][0]["col"] == "总产量",
           f"order_by={r['compute_spec']['order_by'][0]['col']}")

    # 5) count(*):col='*' 跳过校验,不应误报
    r = await _validate({"sheet": "生产明细",
                         "aggregations": [{"col": "*", "func": "count"}]})
    _check("count(*) 不误报 * 不存在", r["spec_issues"] == [])

    # 6) 完全编造的列 → difflib 找不到近似 → 进 issues(交给 LLM correct)
    r = await _validate({"sheet": "生产明细",
                         "filters": [{"col": "客户名", "op": "eq", "value": "x"}]})
    _check("编造列名进 issues(客户名)", bool(r["spec_issues"]),
           f"issues={r['spec_issues']}")

    # 7) compute_spec 为 None → 直接报 issue
    state = DatasetAgentState(messages=[], dataset_id=1, compute_spec=None)
    r = await vmod.validate_spec(state, _FakeRuntime())
    _check("compute_spec=None 报 issue", bool(r["spec_issues"]))


def _run_route_cases() -> None:
    print("_route_after_validate(路由 + 计数封顶 + 兜底):")
    no_issue = DatasetAgentState(messages=[], spec_issues=[])
    has_issue = DatasetAgentState(messages=[], spec_issues=["x"], spec_retry_count=0)
    exhausted = DatasetAgentState(messages=[], spec_issues=["x"], spec_retry_count=MAX_RETRY)
    _check("无 issue → execute_spec", _route_after_validate(no_issue) == "execute_spec")
    _check("有 issue → correct_spec", _route_after_validate(has_issue) == "correct_spec")
    _check(f"重试用尽(={MAX_RETRY})→ execute_spec(兜底)",
           _route_after_validate(exhausted) == "execute_spec")


async def main() -> None:
    # mock 掉打 MySQL 的依赖,纯逻辑离线跑
    vmod.get_dataset_info = _fake_get_dataset_info  # type: ignore[assignment]

    await _run_validate_cases()
    _run_route_cases()

    print()
    if _failures:
        print(f"[FAILED] {len(_failures)} 项未通过 -> {_failures}")
        sys.exit(1)
    print("[OK] ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
