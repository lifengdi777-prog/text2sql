"""把本地 YAML 金标数据集上传成 Langfuse Dataset。

用法:
    uv run python -m evals.langfuse_upload
    uv run python -m evals.langfuse_upload --name text2sql-db-v3 --difficulty easy,medium,hard

要点:
  - dataset_item 的 id 直接用 case 的 id(easy_001 ...),所以**可重复执行**:
    再次上传同一 id 会覆盖更新,不会产生重复条目。
  - input  = {"query": ...}                     —— 喂给图的输入
  - expected_output = {sql / tables / columns / expected_should_continue} —— 评分用的金标
  - metadata = {difficulty / category}          —— 在 Langfuse 里切片分析用
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langfuse import Langfuse  # noqa: E402

from conf.app_config import app_config  # noqa: E402
from evals.runner import load_dataset  # noqa: E402  复用现成的 YAML 加载器

DEFAULT_DATASET_NAME = "text2sql-db-v1"


def _langfuse_client() -> Langfuse:
    cfg = app_config.langfuse
    if not cfg.public_key or not cfg.secret_key:
        raise SystemExit("缺少 Langfuse key,请在 app_config.yaml 的 langfuse 段配置 public_key/secret_key")
    return Langfuse(public_key=cfg.public_key, secret_key=cfg.secret_key, host=cfg.host)


def main() -> None:
    ap = argparse.ArgumentParser(description="Upload YAML golden set to a Langfuse Dataset")
    ap.add_argument("--name", default=DEFAULT_DATASET_NAME, help="Langfuse dataset 名称")
    ap.add_argument("--difficulty", default=None, help="逗号分隔: easy,medium,hard,extra,safety;不传=全部")
    args = ap.parse_args()

    difficulties = args.difficulty.split(",") if args.difficulty else None
    cases = load_dataset(difficulties)
    if not cases:
        raise SystemExit("没有加载到任何 case。")

    lf = _langfuse_client()
    lf.create_dataset(
        name=args.name,
        description="Text2SQL 端到端评估金标集(从 evals/dataset/*.yaml 同步)",
    )

    for case in cases:
        lf.create_dataset_item(
            dataset_name=args.name,
            id=case["id"],  # 用 case id 做主键 → 可重复上传、幂等覆盖
            input={"query": case["query"]},
            expected_output={
                "sql": case.get("gold_sql"),
                "tables": case.get("gold_tables"),
                "columns": case.get("gold_columns"),
                "expected_should_continue": case.get("expected_should_continue"),
                "expected_validate_error": case.get("expected_validate_error"),
            },
            metadata={
                "difficulty": case["difficulty"],
                "category": case.get("category"),
            },
        )

    lf.flush()
    print(f"已上传 {len(cases)} 条到 Langfuse Dataset '{args.name}' → {app_config.langfuse.host}")


if __name__ == "__main__":
    main()
