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
├── dataset/                 # 黄金测试集（YAML，针对 dw 制造库雪花 schema）
│   ├── easy.yaml            # 单表 / 单 JOIN + 基础聚合
│   ├── medium.yaml          # 多表 JOIN + GROUP BY + 比率指标 + TopN / 时间筛选
│   ├── hard.yaml            # 3+ 表 + 窗口 / 子查询 / HAVING / 雪花两跳
│   └── safety.yaml          # 应被拒绝的 query（意图识别 / 写操作 / SQL 注入）
├── metrics/                 # 4 个评估指标
│   ├── execution.py         # Execution Accuracy（金标）
│   ├── exact_match.py       # AST 结构等价
│   ├── schema_linking.py    # 召回 Recall@K
│   └── cost.py              # 延迟 + token
├── runner.py                # 入口：跑整个 graph + 算指标
├── report.py                # 生成 markdown 报告 / 对比基线
├── langfuse_experiment.py   # 可选：把跑批结果上报 Langfuse 做实验对比
├── langfuse_upload.py       # 可选：把数据集上传到 Langfuse
└── baselines/               # 历次结果落盘（{timestamp}_{git_sha}.json）
```

> 数据来源 `docker/mysql/dw.sql`（制造业生产记录，2025–2026 周粒度）。
> **schema 要点**：`factory_name/region/city` 在 `table_factory`，`table_workshop` 只有 `factory_id` ——
> 按"工厂/地区/城市"聚合需 `生产记录→车间→工厂` 两跳 JOIN；`workshop_name` 不唯一（一车间含 A/B 线），
> 按车间统计须 `GROUP BY workshop_id`。写 gold SQL 时务必遵守这两点。

## 用例 YAML 格式

```yaml
- id: medium_001
  difficulty: medium          # easy / medium / hard / safety
  category: groupby_orderby   # 自由打标，用于切片分析
  query: "各工厂的实际产量是多少？按产量从高到低排序"
  gold_sql: |
    SELECT f.factory_name, SUM(p.actual_quantity) AS actual_quantity
    FROM table_production_record p
    JOIN table_workshop w ON p.workshop_id = w.workshop_id
    JOIN table_factory f ON w.factory_id = f.factory_id   # 雪花两跳
    GROUP BY f.factory_name
    ORDER BY actual_quantity DESC
  gold_tables: [table_production_record, table_workshop, table_factory]   # 可选，schema linking 用
  gold_columns: [factory_name, actual_quantity]                          # 可选
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

# 只跑某几档（推荐先跑这三档）
uv run python -m evals.runner --difficulty easy,medium,hard

# 跑完后生成 markdown 报告（baselines/ 下的文件名形如 {timestamp}_{git_sha}.json）
uv run python -m evals.report --result evals/baselines/<after>.json

# 对比两次结果（比如改完 prompt 跑一次，跟上次基线 diff）
uv run python -m evals.report \
    --result evals/baselines/<after>.json \
    --baseline evals/baselines/<before>.json
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

- 数据集越大越准。**先把 easy/medium 各扩到 ~30 条**，再加深 hard。
- 业务术语 / 同义词类（如"良品率=合格率"、"次品率=不良率"、"目标产量=计划产量"，公式见 medium.yaml 头部）
  值得单独维护，是 Text2SQL 最难也最有差异化的地方。
- 新增 gold SQL 后先用 dw 库实跑一遍确认能执行（无"列不存在/分组非法"），再纳入回归。
- `langfuse_experiment.py` / `langfuse_upload.py`：可选,把数据集/跑批结果接到 Langfuse 做实验对比与 trace 回放。
