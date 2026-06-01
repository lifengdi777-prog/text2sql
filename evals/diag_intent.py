"""诊断:把数据集里所有 query 过一遍 parse_query_intention,看哪些被判 should_continue=False。

目的:定位"意图识别误拦合法查询"导致 EX/EM 掉分的问题。
非 safety 的题如果被判 False = 被误拦(本该继续却直接结束,不生成 SQL)。

用法: uv run python -m evals.diag_intent
只调 LLM,不连 DB/Qdrant/ES。
"""
from __future__ import annotations

import asyncio
import glob
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402
from langchain.messages import HumanMessage, SystemMessage  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent.llm import llm  # noqa: E402
from agent.prompts import load_prompt  # noqa: E402


class R(BaseModel):
    should_continue: bool
    guide_queries: list[str]


async def main() -> None:
    prompt = await load_prompt("parse_query_intention")
    sllm = llm.with_structured_output(R, method="json_mode")

    cases = []
    for f in sorted(glob.glob(str(PROJECT_ROOT / "evals" / "dataset" / "*.yaml"))):
        for c in (yaml.safe_load(open(f, encoding="utf-8")) or []):
            cases.append(c)

    sem = asyncio.Semaphore(4)

    async def one(c):
        async with sem:
            try:
                r = await sllm.ainvoke([SystemMessage(content=prompt),
                                        HumanMessage(content=c["query"])])
                return c, r.should_continue, None
            except Exception as e:
                return c, None, str(e)

    results = await asyncio.gather(*[one(c) for c in cases])

    blocked_nonsafety = []
    leaked_safety = []
    for c, sc, err in sorted(results, key=lambda x: x[0]["id"]):
        diff = c["difficulty"]
        flag = ""
        if err:
            flag = f"  ERR {err[:40]}"
        elif diff != "safety" and sc is not True:
            flag = "  <== 误拦!(非safety本该继续)"
            blocked_nonsafety.append(c["id"])
        elif diff == "safety" and sc is not False:
            flag = "  <== 漏拦!(safety本该拒答)"
            leaked_safety.append(c["id"])
        print(f"{c['id']:14s} {diff:7s} should_continue={str(sc):5s}{flag}  | {c['query'][:34]}")

    print("\n" + "=" * 60)
    print(f"非 safety 被误拦: {len(blocked_nonsafety)} 条 -> {blocked_nonsafety}")
    print(f"safety 漏拦:     {len(leaked_safety)} 条 -> {leaked_safety}")


if __name__ == "__main__":
    asyncio.run(main())
