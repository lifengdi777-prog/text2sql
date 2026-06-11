"""Supervisor — 意图路由薄层(多 agent 调度)。

在不改动各子 agent 内部的前提下,加一层父图做分流:
  - route_intent:判断消息是「对已有结果的画图/换图请求」还是「数据查询」;
  - 包装节点:按路由结果把消息交给 chart_agent 或对应的查询 agent 执行。

两个实例(db 与 Excel 是独立功能,入口本身已区分,无需再路由 db/dataset):
  - db_supervisor:      问数页入口   → db_agent | chart_agent
  - dataset_supervisor: 数据集页入口 → dataset_agent | chart_agent

用法:`from agent.supervisor.graph import db_supervisor, dataset_supervisor`。
"""
