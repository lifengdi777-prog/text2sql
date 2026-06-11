"""Attribution Agent — 归因分析("为什么 X 下降了?")。

方法论(五步,每步对应一个节点):
  parse_target        解析归因目标:指标/范围/目标期/对比口径(同比|环比|用户指定)。
                      口径没说 → 澄清卡让用户选,不猜。
  confirm_phenomenon  查目标期 vs 基准期总量,确认现象并量化。
                      基准期没数据 → 说明卡 + 可点的改口径建议,不硬算。
  plan_dimensions     按领域元数据规划 2~4 个拆解维度,每维度一条"两期对比"子问题。
  run_dims            逐维度执行子查询(走注入的 run_query,复用完整查询管线)。
  synthesize          综合各维度对比,定位主要贡献项,产出归因结论。

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
