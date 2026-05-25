# Text2SQL Eval 框架

参考 Spider / BIRD benchmark 的口径，为 wenshu 项目搭建的端到端评估框架。

## 目标

每次改动 prompt / 召回 / 模型时，能用一条命令量化回答以下问题：

- 整体准确率有没有掉？
- 哪个难度档掉了？哪个类别（窗口函数 / 业务术语 / 时间表达）掉了？
- 召回链路是否覆盖到必要的表/列？
- 延迟与 token 成本是否可接受？

## 目录结构

```
evals/
├── dataset/                 # 黄金测试集（YAML）
│   ├── easy.yaml            # 单表 + 简单筛选
│   ├── medium.yaml          # JOIN + GROUP BY
│   ├── hard.yaml            # 3+ 表 + 窗口 / 子查询
│   ├── extra.yaml           # 嵌套 / 反向语义 / 业务术语
│   └── safety.yaml          # 应被拒绝的 query（意图识别 / SQL 注入）
├── metrics/                 # 4 个评估指标
│   ├── execution.py         # Execution Accuracy（金标）
│   ├── exact_match.py       # AST 结构等价
│   ├── schema_linking.py    # 召回 Recall@K
│   └── cost.py              # 延迟 + token
├── runner.py                # 入口：跑整个 graph + 算指标
├── report.py                # 生成 markdown 报告 / 对比基线
└── baselines/               # 历次结果落盘
```

## 用例 YAML 格式

```yaml
- id: medium_002
  difficulty: medium          # easy / medium / hard / extra / safety
  category: join_aggregate    # 自由打标，用于切片分析
  query: "每个产品类别的总销售额是多少？按销售额降序"
  gold_sql: |
    SELECT p.category, SUM(o.order_amount) AS total
    FROM table_order o
    JOIN table_product p ON o.product_id = p.product_id
    GROUP BY p.category
    ORDER BY total DESC
  gold_tables: [table_order, table_product]    # 可选，用于 schema linking 指标
  gold_columns: [order_amount, category]       # 可选
```

`safety.yaml` 用例额外字段：

```yaml
- id: safety_001
  difficulty: safety
  category: should_reject
  query: "今天天气怎么样？"
  expected_should_continue: false              # 期望 parse_query_intention 拒掉
```

## 评估指标

| 指标 | 文件 | 说明 |
|------|------|------|
| **Execution Accuracy (EX)** | execution.py | 跑 gold SQL 和 pred SQL，比较结果集（集合相等，浮点 round 到 4 位）。**最重要的金标。** |
| **Exact Match (EM)** | exact_match.py | sqlglot parse 成 AST，比较 (表集合 / 列集合 / 聚合函数集合)。忽略列序、别名、空白。 |
| **Schema Linking Recall** | schema_linking.py | 从 gold SQL 抽出"应被召回的表/列"，看 `state.recalled_columns` / `state.table_infos` 是否覆盖。 |
| **Latency / Cost** | cost.py | 节点耗时 + 总 token。 |

## 使用

```bash
# 跑完整测试集
uv run python -m evals.runner

# 只跑某个难度档
uv run python -m evals.runner --difficulty hard

# 跑完后生成 markdown 报告
uv run python -m evals.report --result evals/baselines/2026-05-22_2b15dab.json

# 对比两次结果（比如改完 prompt 跑一次，跟上次基线 diff）
uv run python -m evals.report \
    --result evals/baselines/2026-05-22_after.json \
    --baseline evals/baselines/2026-05-22_before.json
```

每次跑完会在 `baselines/` 落一份 `{timestamp}_{git_sha}.json`。

## 工作流建议

1. 改动前先跑一次基线：`uv run python -m evals.runner`
2. 改完代码再跑一次
3. 用 `report.py --baseline ...` 看 diff，重点看：
   - 整体 EX 涨没涨
   - 各难度档是否同步上升（避免 easy 涨 hard 掉）
   - 各类别的 schema linking recall

## 扩展

- 数据集越大越准。**先把 easy/medium 各 20 条扩到 50 条**，再考虑加 hard。
- 业务术语类（`category: business_term`）值得单独维护，是 Text2SQL 最难也最有差异化的地方。
- 接入 Langfuse 后，把每条 case 的 trace_id 也落到 JSON，方便事后回放。
