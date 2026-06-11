"""Attribution Agent — 归因分析("为什么 X 下降了?")。

方法论(五步,每步对应一个节点;对比口径由前端弹层前置给定,不澄清不猜):
  parse_target        解析归因目标:指标/范围/观察期;基准期按口径从候选里代码回填。
  confirm_phenomenon  查观察期 vs 基准期总量,确认现象并量化。
                      基准期没数据 → 说明卡 + 结构化改口径建议,不硬算。
  plan_dimensions     LLM 只选 2~4 个维度名,每维度两条单期分组子问题由代码模板生成。
  run_dims            并发执行子查询(走注入的 run_query),纯代码 join 算贡献度。
  synthesize          LLM 照贡献清单写核心结论;流末发结构化 attribution_result 事件。

跨后端复用的关键:本 agent 不懂 SQL/DuckDB,Context 只注入
  run_query(自包含问题) -> {rows, sql, error}   和   domain_md(领域描述)。
db 入口与 dataset 入口在 supervisor 包装层各自闭包注入。
"""

def __getattr__(name: str):
    # 延迟导入:让 schemas 可被轻量引用,不连带拉起整张图
    if name == "attribution_graph":
        from agent.attribution_agent.graph import attribution_graph as _g
        return _g
    raise AttributeError(f"module 'agent.attribution_agent' has no attribute {name!r}")


__all__ = ["attribution_graph"]
